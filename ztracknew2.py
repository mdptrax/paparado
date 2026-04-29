from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from pydantic import BaseModel
from datetime import datetime, date, timedelta
import json
from fastapi.responses import FileResponse
import os
import pandas as pd
from fastapi import UploadFile, File
import shutil
from fastapi.responses import StreamingResponse
import io
from docxtpl import DocxTemplate
from io import BytesIO


# ================= DATABASE =================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trax.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ================= MODELS =================
from sqlalchemy import DateTime

class TestAutoclave(Base):
    __tablename__ = "test_autoclave"

    id = Column(Integer, primary_key=True)
    autoclave = Column(String)
    tipo = Column(String)
    codice = Column(String)
    esito = Column(String)
    operator = Column(String)
    time = Column(DateTime)  # 🔥 CAMBIATO

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    hospital = Column(String)
    status = Column(String)
    history = Column(Text)
    autoclave = Column(String)

    unita_operativa = Column(String)  # 🔥 NUOVO
    nr = Column(String)               # 🔥 NUOVO
    us = Column(String)               # 🔥 NUOVO

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    password = Column(String)
    role = Column(String)

class Lotto(Base):
    __tablename__ = "lotti"
    id = Column(Integer, primary_key=True)
    codice = Column(String)
    autoclave = Column(String)
    operator = Column(String)
    start_time = Column(String)
    end_time = Column(String)
    items = Column(Text)


class Kit(Base):
    __tablename__ = "kit"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String, unique=True, index=True)


# 🔥 QUI
Base.metadata.create_all(bind=engine)

# ================= SCHEMI =================
class LoginData(BaseModel):
    username: str
    password: str

class RegisterData(BaseModel):
    username: str
    password: str
    role: str = "user"

class ItemCreate(BaseModel):
    name: str
    hospital: str
    operator: str
    unita_operativa: str = ""
    nr: str = ""
    us: str = ""

class StatusUpdate(BaseModel):
    operator: str
    status: str

class AutoclaveLoad(BaseModel):
    id: int
    macchina: str
    operator: str

# ================= APP =================
from contextlib import asynccontextmanager



app = FastAPI()
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= KIT EXCEL =================

# ================= STARTUP =================
@app.get("/download-log")
def download_log(db: Session = Depends(get_db)):

    items = db.query(Item).all()

    data = []
    for i in items:
        data.append({
            "id": i.id,
            "nome_kit": i.name,
            "ospedale": i.hospital,
            "operatore": i.operator,
            "unita_operativa": i.unita_operativa,
            "nr": i.nr,
            "us": i.us,
            "status": i.status
        })

    df = pd.DataFrame(data)

    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=log_produzione.xlsx"
        }
    )

@app.post("/test-autoclave")
def add_test(data: dict, db: Session = Depends(get_db)):

    test = TestAutoclave(
    autoclave=data["autoclave"],
    tipo=data["tipo"],
    codice=data["codice"],
    esito=data["esito"],
    operator=data["operator"],
    time=datetime.now()  # 🔥 CAMBIATO
)
    
    db.add(test)
    db.commit()

    return {"ok": True}

@app.get("/test-autoclave/report/{a}")
def report_autoclave(a: str, db: Session = Depends(get_db)):

    tests = db.query(TestAutoclave).filter(
        TestAutoclave.autoclave == a
    ).all()

    return tests

# ================= API KIT =================

from docxtpl import DocxTemplate
import tempfile

@app.get("/genera-report-autoclave/{autoclave}")
def genera_report(autoclave: str, db: Session = Depends(get_db)):

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    template_path = os.path.join(
        BASE_DIR,
        "SchedaControlloAutoclave_TEMPLATE_OK.docx"
    )

    print("📁 PATH:", template_path)
    print("📁 ESISTE:", os.path.exists(template_path))

    if not os.path.exists(template_path):
        raise HTTPException(500, f"Template NON trovato: {template_path}")

    doc = DocxTemplate(template_path)

    tests = db.query(TestAutoclave).filter(
        TestAutoclave.autoclave == autoclave
    ).all()

    def get_test(tipo):
        for t in tests:
            if tipo.lower() in t.tipo.lower():
                return t.codice
        return "NON ESEGUITO"

    context = {
        "data": datetime.now().strftime("%d/%m/%Y"),
        "autoclave": autoclave,
        "operatore": tests[0].operator if tests else "-",
        "riscaldamento": get_test("riscaldamento"),
        "vuoto": get_test("vuoto"),
        "bowie_dick": get_test("bowie"),
        "helix_test": get_test("helix"),
        "prova_biologica": get_test("biologica"),
        "note": ""
    }

    buffer = BytesIO()
    doc.render(context)
    doc.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename=Report_{autoclave}.docx"
        }
    )

    
@app.post("/doc")
async def upload_excel(file: UploadFile, db: Session = Depends(get_db)):
    import pandas as pd

    df = pd.read_excel(file.file)

    # 🔥 pulizia colonne
    df.columns = df.columns.str.strip().str.lower()

    if "nome" not in df.columns:
        raise HTTPException(400, "Colonna 'nome' mancante")

    inseriti = 0

    for nome in df["nome"].dropna():
        nome = str(nome).strip()

        esiste = db.query(Kit).filter(Kit.nome == nome).first()
        if not esiste:
            db.add(Kit(nome=nome))
            inseriti += 1

    db.commit()

    return {
        "ok": True,
        "inseriti": inseriti
    }
    
@app.get("/kit")
def get_kit(db: Session = Depends(get_db)):
    kits = db.query(Kit).order_by(Kit.nome).all()
    return [k.nome for k in kits]


from fastapi import Query


# ================= UTILS =================
def now():
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def add_history(item, operator, phase):
    try:
        history = json.loads(item.history) if item.history else []
    except:
        history = []
    history.append({
        "phase": phase,
        "operator": operator,
        "time": now()
    })
    item.history = json.dumps(history)

# ================= HTML =================
html = """
<!DOCTYPE html>
<html>
<head>
<title>ZTrack</title>

<style>

.popup {
  position: fixed;
  top: 20px;
  right: 20px;
  background: #2ecc71;
  color: white;
  padding: 15px 20px;
  border-radius: 10px;
  font-weight: bold;
  display: none;
  box-shadow: 0 5px 15px rgba(0,0,0,0.2);
  animation: fadeInOut 3s ease;
}

@keyframes fadeInOut {
  0% {opacity: 0; transform: translateY(-20px);}
  10% {opacity: 1; transform: translateY(0);}
  90% {opacity: 1;}
  100% {opacity: 0; transform: translateY(-20px);}
}

</style>
<style>
:root {
 --bg-card:#3498db;
 --text-color:white;
 --btn-bg:#2c3e50;
 --btn-text:white;
}

body {font-family:Arial;margin:0;}

.navbar {background:var(--btn-bg);padding:10px;display:none;}
.navbar button {
 background:transparent;color:var(--btn-text);
 border:none;margin-right:10px;cursor:pointer;
}

.page {display:none;padding:20px;}
.active {display:block;}

.card {
 background:var(--bg-card);
 color:var(--text-color);
 padding:10px;margin:10px;
 border-radius:10px;
}

button {
 background:var(--btn-bg);
 color:var(--btn-text);
 border:none;padding:5px 10px;
 margin-top:5px;border-radius:5px;cursor:pointer;
}
.form-container {
  background: #ffffff;
  padding: 20px;
  border-radius: 12px;
  box-shadow: 0 4px 10px rgba(0,0,0,0.08);
  margin-bottom: 20px;
}

.form-container h3 {
  margin-bottom: 15px;
  color: #333;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 15px;
}

.form-group {
  display: flex;
  flex-direction: column;
}

.form-group label {
  font-size: 13px;
  margin-bottom: 5px;
  color: #555;
}

.form-group input {
  padding: 8px;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 14px;
}

.form-group input:focus {
  outline: none;
  border-color: #007bff;
}

.btn-primary {
  margin-top: 15px;
  padding: 10px;
  border: none;
  border-radius: 8px;
  background: #007bff;
  color: white;
  font-weight: bold;
  cursor: pointer;
  width: 100%;
}

.btn-primary:hover {
  background: #0056b3;
}
</style>
</head>

<body>

<div id="loginPage" class="page active" style="display:flex;justify-content:center;align-items:center;height:100vh;background:#ecf0f1;">

<div style="background:white;padding:30px;border-radius:15px;box-shadow:0 5px 15px rgba(0,0,0,0.2);width:300px;text-align:center;">

<h2 style="margin-bottom:20px;">ZTrack</h2>

<input id="username" placeholder="Username" style="width:100%;padding:8px;margin-bottom:10px;"><br>
<input id="password" type="password" placeholder="Password" style="width:100%;padding:8px;margin-bottom:20px;"><br>

<button onclick="login()">ENTRA</button>

<br><br>

<button onclick="openRegister()" style="background:#3498db;width:100%;padding:10px;">
 Registrati
</button>

</div>
</div>

<div id="registerModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);justify-content:center;align-items:center;z-index:1000;">

<div style="background:white;padding:30px;border-radius:15px;width:300px;text-align:center;">

<h3>Registrazione</h3>

<input id="reg_username" placeholder="Username" style="width:100%;padding:8px;margin-bottom:10px;"><br>
<input id="reg_password" type="password" placeholder="Password" style="width:100%;padding:8px;margin-bottom:20px;"><br>

<button onclick="register()">Crea Account</button>
<br><br>
<button onclick="closeRegister()">Chiudi</button>

</div>
</div>

<div class="navbar" id="navbar" style="display:flex;justify-content:space-between;align-items:center;">

  <!-- MENU -->
  <div>
    <button onclick="showPage('test_autoclave')">Test Autoclave</button>
    <button onclick="showPage('accettazione')">Accettazione</button>
    <button onclick="showPage('lavaggio')">Lavaggio</button>
    <button onclick="showPage('confezionamento')">Confezionamento</button>
    <button onclick="showPage('autoclavi')">Autoclavi</button>
    <button onclick="showPage('sterilizzati')">Sterilizzati</button>
    <button onclick="showPage('storico_test')">Storico Test</button>
    <button onclick="logout()">Logout</button>
    <button onclick="downloadLog()">📊 Produzione</button>
  </div>

  <!-- 👇 NOME UTENTE -->
  <div id="userDisplay" style="
    color:white;
    font-weight:bold;
    background:#1abc9c;
    padding:5px 10px;
    border-radius:10px;
  "></div>

</div>

<div id="accettazione" class="page">
<h2>Accettazione</h2>
<div class="form-container">

  <h3>Accettazione Kit</h3>

  <div class="form-grid">

    <div class="form-group">
      <label>Nome Kit</label>
<input id="kit_name" list="kit_suggestions" 
       placeholder="Seleziona o inserisci"
       oninput="checkForm()"
       oncontextmenu="selezionaFileExcel(event)">      <datalist id="kit_suggestions"></datalist>
    </div>

    <div class="form-group">
      <label>Ospedale</label>
      <input id="kit_hospital" value="PAPARDO" readonly>
    </div>

    <div class="form-group">
      <label>Unità Operativa</label>
      <input id="unita_operativa" list="uo_suggestions" placeholder="U.O." oninput="checkForm()">
<datalist id="uo_suggestions"></datalist>
    </div>

    <div class="form-group">
      <label>NR</label>
      <input id="nr" placeholder="Numero" oninput="checkForm()">
    </div>

    <div class="form-group">
      <label>US</label>
      <input id="us" placeholder="Unità Sterile" oninput="checkForm()">
    </div>

  </div>

  <button id="btnAddKit" onclick="addItem()" disabled>
 ➕ Aggiungi Kit
</button>

</div>
<div id="list_accettazione"></div>
</div>

<div id="storico_test" class="page">

<h2>Storico Test Autoclave</h2>

<input type="date" id="data_ricerca">

<br><br>

<select id="auto_ricerca">
  <option value="">Tutte</option>
  <option value="A1">A1</option>
  <option value="A2">A2</option>
  <option value="A3">A3</option>
</select>

<br><br>

<button onclick="stampaStorico()">🖨️ Stampa</button>

<br><br>

<div id="storico_result"></div>

</div>

<div id="lavaggio" class="page"><h2>Lavaggio</h2><div id="list_lavaggio"></div></div>
<div id="confezionamento" class="page"><h2>Confezionamento</h2><div id="list_confezionamento"></div></div>

<div id="autoclavi" class="page">
<h2>Autoclavi</h2>
<h3>A1</h3><div id="auto1"></div>
<h3>A2</h3><div id="auto2"></div>
<h3>A3</h3><div id="auto3"></div>
</div>

<div id="sterilizzati" class="page">
<h2>Lotti Sterilizzazione</h2>

<input type="date" id="filtro_data" onchange="loadLotti()">
<button onclick="resetFiltro()">Oggi</button>

<br><br>

<div id="list_sterilizzati"></div>
</div>

<div id="test_autoclave" class="page">
<h2>Test Autoclave</h2>

<label>Autoclave:</label><br>
<select id="test_auto_select">
<option value="A1">A1</option>
<option value="A2">A2</option>
<option value="A3">A3</option>
</select>

<br><br>

<label>Tipo Test:</label><br>
<select id="test_tipo">
<option>Test di riscaldamento</option>
<option>Test di vuoto</option>
<option>Helix Test</option>
<option>Bowie Dick</option>
<option>Prova biologica</option>
</select>

<br><br>

<label>Numero lotto:</label><br>
<input id="test_lotto" placeholder="Inserisci lotto">

<br><br>

<label>Esito:</label><br>
<select id="test_esito">
<option value="OK">OK</option>
<option value="KO">KO</option>
</select>

<br><br>

<button onclick="startTestCycle()">Registra Test</button>

<div id="test_result"></div>

<!-- 🔥 REPORT DENTRO LA PAGINA -->
<br><br>

<h3>Report</h3>

<button onclick="loadReport('A1')">Report A1</button>
<button onclick="loadReport('A2')">Report A2</button>
<button onclick="loadReport('A3')">Report A3</button>

<div id="report_test"></div>

</div>

<script>
let currentUser = null;
let refreshInterval = null;
let KIT_DB = [];
let UO_DB = [
"PRONTO SOCCORSO",
"MEDICINA D’URGENZA",
"RIANIMAZIONE",
"CHIRURGIA REPARTO",
"ORTOPEDIA REPARTO",
"UROLOGIA REPARTO",
"CARDIOLOGIA UTIC",
"ANTALGICA REPARTO",
"NEUROLOGIA",
"CGA",
"SPINALE",
"ORTOPEDIA S.O.",
"CHIRURGIA GENERALE S.O.",
"UROLOGIA S.O.",
"CARDIOLOGIA S.O.",
"ANTALGICA S.O."
];
function startAutoRefresh() {
  if (refreshInterval) clearInterval(refreshInterval);

  refreshInterval = setInterval(() => {

    // 👇 refresh SOLO se NON sei nel login
    if (!currentUser) return;

    // 👇 refresh SOLO se pagina visibile
    if (document.hidden) return;

    console.log("🔄 Refresh intelligente...");
    loadAll();

  }, 2000);
}

async function loadStorico(){

  let data = document.getElementById("data_ricerca").value;
  let auto = document.getElementById("auto_ricerca").value;

let url = "/test/storico?";

  if(data){
    url += "data=" + data + "&";
  }

  if(auto){
    url += "autoclave=" + auto;
  }

  console.log("URL chiamata:", url);

  let r = await fetch(url);
  let result = await r.json();

  let div = document.getElementById("storico_result");
  div.innerHTML = "";

  if(result.length === 0){
    div.innerHTML = "<p>Nessun risultato</p>";
    return;
  }

  result.forEach(t => {

    let color = t.esito === "OK" ? "green" : "red";

    div.innerHTML += `
      <div class="card">
        <b>${t.tipo}</b><br>
        Autoclave: ${t.autoclave}<br>
        Lotto: ${t.codice}<br>
        Esito: <span style="color:${color}">${t.esito}</span><br>
        Operatore: ${t.operator}<br>
        Data: ${t.time}
      </div>
    `;
  });
}

async function loadKitSuggestions(){
  try {
    let res = await fetch("/kit");
    let data = await res.json();

    console.log("KIT:", data);

    KIT_DB = data; // 🔥🔥🔥 QUESTA È LA CHIAVE

    let dl = document.getElementById("kit_suggestions");

    if(!dl){
      console.error("❌ datalist non trovato");
      return;
    }

    dl.innerHTML = "";

    data.forEach(k=>{
      let opt = document.createElement("option");
      opt.value = k;
      dl.appendChild(opt);
    });

    console.log("✅ DATALIST POPOLATA");

  } catch(err){
    console.error("❌ ERRORE:", err);
  }
}
function stampaStorico(){

  console.log("STAMPA PARTITA");

  let contenuto = document.getElementById("storico_result").innerHTML;

  let data = document.getElementById("data_ricerca").value;
  let auto = document.getElementById("auto_ricerca").value;

  let finestra = window.open("", "", "width=900,height=700");

  finestra.document.write(`
    <html>
    <head>
      <title>Report Storico Test</title>

      <style>
        body {
          font-family: Arial;
          padding: 20px;
        }

        h2 {
          text-align: center;
        }

        .card {
          border: 1px solid #000;
          padding: 10px;
          margin-bottom: 10px;
        }
      </style>
    </head>

    <body>

      <h2>Report Storico Test Autoclave</h2>

      <p><b>Data:</b> ${data || "Tutte"}</p>
      <p><b>Autoclave:</b> ${auto || "Tutte"}</p>

      <hr>

      ${contenuto}

    </body>
    </html>
  `);

  finestra.document.close();
  finestra.print();
}

  const dataInput = document.getElementById("data_ricerca");
  const autoSelect = document.getElementById("auto_ricerca");

  if(dataInput){
    dataInput.addEventListener("change", loadStorico);

  }

  if(autoSelect){
    autoSelect.addEventListener("change", loadStorico);
  }



async function loadReport(a){
 let r = await fetch('/test-autoclave/report/' + a);
 let data = await r.json();

 let div = document.getElementById("report_test");
 div.innerHTML = "";

 if(data.length === 0){
   div.innerHTML = "<p>Nessun test registrato</p>";
   return;
 }

 div.innerHTML += `<h3>Autoclave ${a}</h3>`;

 data.forEach(t => {

  let color = t.esito === "OK" ? "green" : "red";

  div.innerHTML += `
    <div class="card">
      <b>${t.tipo}</b><br>
      Lotto: ${t.codice}<br>
      Esito: <span style="color:${color}">${t.esito}</span><br>
      Operatore: ${t.operator}<br>
      Data: ${t.time}
    </div>
  `;
 });

 div.innerHTML += `
   <button onclick="printReport('${a}')">
    🖨️ Stampa Report
   </button>
 `;
}

async function login(){
 try{
   let user = document.getElementById("username").value;
   let pass = document.getElementById("password").value;

   console.log("Invio login:", user, pass);

   let r = await fetch('/login',{
  method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({
    username: user,
    password: pass
  })
});

   console.log("Status:", r.status);

   if(r.status != 200){
     alert("Login fallito");
     return;
   }

   currentUser = await r.json();

   // 🔥 MOSTRA UTENTE
document.getElementById("userDisplay").innerText =
  "👤 " + currentUser.username;

   // 🔥 SALVA SESSIONE
localStorage.setItem("user", JSON.stringify(currentUser));


document.getElementById("loginPage").style.display = "none";
document.getElementById("loginPage").classList.remove("active");

document.getElementById("navbar").style.display = "block";

// 🔥 usa showPage (ora corretto)
showPage('accettazione');

// 🔥 forza refresh dati
loadAll();

startAutoRefresh();

 }catch(e){
   console.error("ERRORE LOGIN:", e);
   alert("Errore JS login");
 }
}
function logout(){

  console.log("🔒 Logout cliccato");

  // 🔥 1. CANCELLA SESSIONE SALVATA
  localStorage.removeItem("user");

  // 🔥 2. RESET VARIABILI
  currentUser = null;

  // 🔥 3. FERMA AUTO REFRESH
  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }

  // 🔥 4. NASCONDI NAVBAR
  document.getElementById("navbar").style.display = "none";

  // 🔥 5. NASCONDI PAGINE
  document.querySelectorAll('.page').forEach(p => {
    p.classList.remove('active');
    p.style.display = "none";
  });

  // 🔥 6. MOSTRA LOGIN
  let loginPage = document.getElementById("loginPage");
  loginPage.style.display = "flex";
  loginPage.classList.add('active');

  // 🔥 7. PULISCI CAMPI
  document.getElementById("username").value = "";
  document.getElementById("password").value = "";

  // 🔥 8. PULISCI NOME UTENTE
  document.getElementById("userDisplay").innerText = "";

}
async function register(){

 let username = document.getElementById("reg_username").value;
 let password = document.getElementById("reg_password").value;

 if(!username || !password){
   alert("Inserisci username e password");
   return;
 }

 try{

   let r = await fetch('/register',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      username: username,
      password: password
    })
   });

   if(!r.ok){
     let err = await r.text();
     alert("Errore: " + err);
     return;
   }

   alert("✅ Account creato");
   closeRegister();

 }catch(e){
   console.error("ERRORE REGISTER:", e);
   alert("Errore rete");
 }
}



async function showPage(p){
 if(p === "storico_test"){
  loadStorico();
}
 if(!currentUser) return

 document.querySelectorAll('.page').forEach(x=>{
   x.classList.remove('active');
   x.style.display = "none";
 });

 let page = document.getElementById(p);

 if(p === "loginPage"){
   page.style.display = "flex";
 } else {
   page.style.display = "block";
 }

 page.classList.add('active');

 // 💥 SOLO QUI
 if(p === "accettazione"){
   setTimeout(loadKitSuggestions, 200);  // 🔥 fondamentale
   loadUOSuggestions();
}

 // opzionale
 if(p === "reportPage"){
   loadAll();
 }
}

async function addItem(){
 if(!currentUser) return;

 let nome = document.getElementById("kit_name").value;
 let uo = document.getElementById("unita_operativa").value.toUpperCase();
 let nr = document.getElementById("nr").value;
 let us = document.getElementById("us").value;

 checkForm();

 if(!nome || !uo || !nr || !us || !document.getElementById("kit_hospital").value){
 alert("Compila tutti i campi!");
 return;
}

 

 await fetch('/items',{
  method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({
   name: nome,
   hospital: document.getElementById("kit_hospital").value,
   operator: currentUser.username,
   unita_operativa: uo,
   nr: nr,
   us: us
  })
 });

 // reset campi
 // reset campi
document.getElementById("kit_name").value = "";
document.getElementById("unita_operativa").value = "";
document.getElementById("nr").value = "";
document.getElementById("us").value = "";

checkForm(); // 🔥 QUESTO È IL CUORE DEL FIX

loadAll();
// 🔥 MOSTRA POPUP SUCCESSO
let popup = document.getElementById("popupSuccess");
popup.style.display = "block";

setTimeout(() => {
  popup.style.display = "none";
}, 3000);
}
  
function loadUOSuggestions(){
 let dl = document.getElementById("uo_suggestions");
 dl.innerHTML = "";

 UO_DB.forEach(uo=>{
   dl.innerHTML += `<option value="${uo}">`;
 });
}

function downloadLog(){
 window.location.href = '/download-log';
}
async function updateStatus(id,status){
 if(!currentUser) return;

 await fetch('/items/'+id+'/status',{
  method:'PUT',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({operator:currentUser.username,status:status})
 });

 loadAll();
}
async function loadAutoclave(id,macchina){
 if(!currentUser) return;

 await fetch('/autoclave/load',{
  method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({id:id,macchina:macchina,operator:currentUser.username})
 });

 loadAll();
}

async function startCycle(a, btn){
 if(!currentUser) return;
 if(btn.disabled) return;

 let codice = prompt("Inserisci numero lotto:");

 if(!codice){
   alert("Numero lotto obbligatorio");
   return;
 }

 btn.disabled = true;
 btn.innerText = "⏳ Avvio...";

 try{
   let r = await fetch('/autoclave/start/'+a+
     '?operator='+currentUser.username+
     '&codice='+encodeURIComponent(codice),
   {
     method:'POST'
   });

   if(!r.ok){
     alert(await r.text());
     btn.disabled = false;
     btn.innerText = "▶️ Avvia ciclo";
     return;
   }

   alert("Ciclo avviato su " + a);
   btn.innerText = "✅ Avviato";

 }catch(e){
   alert("Errore rete");
   btn.disabled = false;
   btn.innerText = "▶️ Avvia ciclo";
 }
}

async function finishCycle(a){
 if(!currentUser) return;

 let r=await fetch('/autoclave/end/'+a,{method:'POST'});
 let lotto=await r.json();

 showPrint(lotto);
 loadAll();
}

function showPrint(l){
 let w=window.open('');
 w.document.write(`
  <h2>Lotto Sterilizzazione</h2>
  Codice: ${l.codice}<br>
  Autoclave: ${l.autoclave}<br>
  Operatore: ${l.operator}<br>
  Inizio: ${l.start_time}<br>
  Fine: ${l.end_time}<br>

  ${l.items.map(i=>`
    <h4>${i.name}</h4>
    <ul>
      ${i.history.map(h=>`
        <li>${h.phase} - ${h.operator} - ${h.time}</li>
      `).join("")}
    </ul>
  `).join("")}

  <button onclick="window.print()">STAMPA</button>
 `);
}

function reprintLotto(id){
 let l = LOTTI_GLOBAL.find(x => x.id === id);
 if(!l) return alert("Lotto non trovato");

 let items = JSON.parse(l.items);

 let w=window.open('');
 w.document.write(`
  <h2>Lotto Sterilizzazione</h2>
  Codice: ${l.codice}<br>
  Autoclave: ${l.autoclave}<br>
  Operatore: ${l.operator}<br>
  Inizio: ${l.start_time}<br>
  Fine: ${l.end_time}<br>

  ${items.map(i=>`
    <h4>${i.name}</h4>
    <ul>
      ${i.history.map(h=>`
        <li>${h.phase} - ${h.operator} - ${h.time}</li>
      `).join("")}
    </ul>
  `).join("")}

  <button onclick="window.print()">STAMPA</button>
 `);
}

function render(list,id,phase){
 let div=document.getElementById(id);
 div.innerHTML="";

 list.forEach(i=>{
  let btn="";
  if(phase=="lav") btn=`<button onclick="updateStatus(${i.id},'lavato')">Lavato</button>`;
  if(phase=="conf") btn=`<button onclick="updateStatus(${i.id},'confezionato')">Confeziona</button>`;
  if(phase=="auto") btn=`
   <button onclick="loadAutoclave(${i.id},'A1')">A1</button>
   <button onclick="loadAutoclave(${i.id},'A2')">A2</button>
   <button onclick="loadAutoclave(${i.id},'A3')">A3</button>`;

  div.innerHTML+=`
<div class="card">
 ${i.name}<br>
 UO: ${i.unita_operativa || "-"}<br>
 NR: ${i.nr || "-"}<br>
 US: ${i.us || "-"}<br>
 ${btn}
</div>`;
 });
}

function loadAutoclavi(data){
 auto1.innerHTML="";
 auto2.innerHTML="";
 auto3.innerHTML="";

 ["A1","A2","A3"].forEach(a=>{
  let div = document.getElementById("auto"+a.replace("A",""));

  div.innerHTML += `
    <button onclick="startCycle('${a}', this)">▶️ Avvia ciclo</button>
    <button onclick="finishCycle('${a}')">⏹️ Fine ciclo</button>
    <hr>
  `;

  data.filter(i=>i.status=="in_autoclave" && i.autoclave==a)
  .forEach(i=>{
    div.innerHTML += `<div class="card">${i.name}</div>`;
  });
 });
}

async function loadLotti(){
 let data = document.getElementById("filtro_data").value;

 let url = "/lotti";
 if(data){
   url += "?date=" + data;
 }

 let r = await fetch(url);
 let lotti = await r.json();

 LOTTI_GLOBAL = lotti;

 let div=document.getElementById("list_sterilizzati");
 div.innerHTML="";

 if(lotti.length === 0){
   div.innerHTML = "<p>Nessun lotto trovato</p>";
   return;
 }

 lotti.forEach(l=>{
  let items=JSON.parse(l.items);

  div.innerHTML+=`
   <div class="card">
    <b>${l.codice}</b><br>
    Autoclave: ${l.autoclave}<br>
    Operatore: ${l.operator}<br>
    Inizio: ${l.start_time}<br>
    Fine: ${l.end_time}<br>
    Kit: ${items.map(i=>i.name).join(", ")}<br><br>

    <button onclick="reprintLotto(${l.id})">
     🖨️ Ristampa
    </button>
   </div>`;
 });
}

async function loadAll(){
 if(!currentUser) return;

 let r=await fetch('/items');
 let d=await r.json();

 render(d.filter(i=>i.status=='accettato'),"list_accettazione","lav");
 render(d.filter(i=>i.status=='lavato'),"list_lavaggio","conf");
 render(d.filter(i=>i.status=='confezionato'),"list_confezionamento","auto");
 loadAutoclavi(d);
 loadLotti();
}

function resetFiltro(){
 document.getElementById("filtro_data").value = "";
 loadLotti();
}

function openRegister(){
 document.getElementById("registerModal").style.display = "flex";
}

function closeRegister(){
 document.getElementById("registerModal").style.display = "none";
}
function test(){
 alert("CLICK FUNZIONA");
}

function checkForm(){
 let nome = document.getElementById("kit_name").value.trim();
 let uo = document.getElementById("unita_operativa").value.trim();
 let nr = document.getElementById("nr").value.trim();
 let us = document.getElementById("us").value.trim();

 let btn = document.getElementById("btnAddKit");

 if(nome && uo && nr && us){
   btn.disabled = false;
 } else {
   btn.disabled = true;
 }
}

async function startTestCycle(){
 if(!currentUser) return;

 let a = document.getElementById("test_auto_select").value;
 let tipo = document.getElementById("test_tipo").value;
 let codice = document.getElementById("test_lotto").value;
 let esito = document.getElementById("test_esito").value;

 if(!codice){
   alert("Inserisci lotto");
   return;
 }

 try{
   let r = await fetch('/test-autoclave', {
     method:'POST',
     headers:{'Content-Type':'application/json'},
     body: JSON.stringify({
       autoclave: a,
       tipo: tipo,
       codice: codice,
       esito: esito,
       operator: currentUser.username
     })
   });

   let text = await r.text();

   if(!r.ok){
     document.getElementById("test_result").innerHTML =
       "<span style='color:red'>" + text + "</span>";
     return;
   }

   document.getElementById("test_result").innerHTML =
     "<span style='color:green'>✅ Test registrato</span>";

   // reset campi
   document.getElementById("test_lotto").value = "";

 }catch(e){
   document.getElementById("test_result").innerHTML =
     "<span style='color:red'>Errore rete</span>";
 }
}

async function printReport(a){
  try {
    const res = await fetch(`/genera-report-autoclave/${a}`);

    if (!res.ok) {
      alert("Errore generazione report");
      return;
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);

    const aTag = document.createElement("a");
    aTag.href = url;
    aTag.download = `Report_${a}.docx`;
    document.body.appendChild(aTag);
    aTag.click();
    aTag.remove();

    window.URL.revokeObjectURL(url);

  } catch (e) {
    console.error(e);
    alert("Errore download");
  }
}
loadUOSuggestions();

document.addEventListener("DOMContentLoaded", () => {
  console.log("🚀 pagina caricata");
  loadKitSuggestions();

  // 🔥 RIPRISTINO SESSIONE DOPO F5
  let savedUser = localStorage.getItem("user");

  if (savedUser) {
  currentUser = JSON.parse(savedUser);

  // 🔥 AGGIUNGI QUESTA RIGA
  document.getElementById("userDisplay").innerText =
    "👤 " + currentUser.username;

  document.getElementById("loginPage").style.display = "none";
  document.getElementById("loginPage").classList.remove("active");

  document.getElementById("navbar").style.display = "block";

  showPage('accettazione');
  loadAll();

  startAutoRefresh();
}
});
</script>
<div id="popupSuccess" class="popup">
  ✅ Kit accettato correttamente
</div>
<script>

</script>
</body>
</html>
"""

# ================= API =================
@app.get("/test/storico")
def storico_test(data: str = None, autoclave: str = None, db: Session = Depends(get_db)):

    query = db.query(TestAutoclave)

    if data:
        d = datetime.strptime(data, "%Y-%m-%d")
        next_day = d + timedelta(days=1)

        query = query.filter(
            TestAutoclave.time >= d,
            TestAutoclave.time < next_day
        )

    if autoclave:
        query = query.filter(TestAutoclave.autoclave == autoclave)

    return query.all()

@app.get("/test/report/{autoclave}")
def get_report(autoclave: str, db: Session = Depends(get_db)):

    today = date.today()

    tests = db.query(TestAutoclave).filter(
        TestAutoclave.autoclave == autoclave,
        TestAutoclave.time >= today
    ).all()

    return tests

@app.get("/", response_class=HTMLResponse)
def home():
    return html

@app.post("/login")
def login(data: LoginData, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        User.username == data.username,
        User.password == data.password
    ).first()
    if not user:
        raise HTTPException(401, "Errore login")
    return {"username": user.username}

@app.post("/register")
def register(data: RegisterData, db: Session = Depends(get_db)):

    existing = db.query(User).filter(User.username == data.username).first()
    if existing:
        raise HTTPException(400, "Utente già esistente")

    new_user = User(
        username=data.username,
        password=data.password,
        role=data.role
    )

    db.add(new_user)
    db.commit()

    return {"ok": True}

@app.get("/items")
def items(db: Session = Depends(get_db)):
    return db.query(Item).all()

@app.post("/items")
def create_item(data: ItemCreate, db: Session = Depends(get_db)):
    item = Item(
        name=data.name,
        hospital=data.hospital,
        status="accettato",
        unita_operativa=data.unita_operativa,
        nr=data.nr,
        us=data.us
    )

    add_history(item, data.operator, "accettato")
    db.add(item)
    db.commit()
    return {"ok": True}

# ===== SALVATAGGIO EXCEL =====


    

@app.put("/items/{item_id}/status")
def update_status(item_id: int, data: StatusUpdate, db: Session = Depends(get_db)):
    item = db.query(Item).filter(Item.id == item_id).first()

    if not item:
        raise HTTPException(404, "Item non trovato")

    item.status = data.status

    add_history(item, data.operator, data.status)

    db.commit()

    return {"ok": True}

@app.post("/autoclave/load")
def load_auto(data: AutoclaveLoad, db: Session = Depends(get_db)):
    item = db.query(Item).filter(Item.id == data.id).first()
    if not item:
        raise HTTPException(404, "Item non trovato")

    item.status = "in_autoclave"
    item.autoclave = data.macchina
    add_history(item, data.operator, "caricato "+data.macchina)
    db.commit()
    return {"ok": True}

from fastapi import Query

from fastapi import Query

@app.post("/autoclave/start/{a}")
def start_auto(a: str, operator: str, codice: str = Query(...), db: Session = Depends(get_db)):

    existing = db.query(Lotto).filter(
        Lotto.autoclave == a,
        Lotto.end_time == None
    ).first()

    if existing:
        raise HTTPException(400, "Ciclo già attivo")

    items = db.query(Item).filter(
        Item.status == "in_autoclave",
        Item.autoclave == a
    ).all()

    if not items:
        raise HTTPException(400, "Nessun item nell'autoclave")

    # 🚫 blocco duplicati lotto SOLO per autoclave
    existing_code = db.query(Lotto).filter(
        Lotto.codice == codice,
        Lotto.autoclave == a
    ).first()

    if existing_code:
        raise HTTPException(400, f"Numero lotto già usato su {a}")

    lotto = Lotto(
        codice=codice,
        autoclave=a,
        operator=operator,
        start_time=now(),
        end_time=None,
        items=json.dumps([
            {
                "name": i.name,
                "history": json.loads(i.history) if i.history else []
            }
            for i in items
        ])
    )

    db.add(lotto)
    db.commit()

    return {"ok": True}

@app.post("/autoclave/end/{a}")
def end_auto(a: str, db: Session = Depends(get_db)):
    items = db.query(Item).filter(
        Item.status=="in_autoclave",
        Item.autoclave==a
    ).all()

    if not items:
        raise HTTPException(400, "Nessun item in autoclave")

    lotto = db.query(Lotto).filter(
        Lotto.autoclave==a,
        Lotto.end_time==None
    ).order_by(Lotto.id.desc()).first()

    if not lotto:
        raise HTTPException(400, "Ciclo non avviato")

    lotto.end_time = now()

    for i in items:
        i.status="sterilizzato"

    db.commit()

    return {
        "codice":lotto.codice,
        "autoclave":a,
        "operator":lotto.operator,
        "start_time":lotto.start_time,
        "end_time":lotto.end_time,
        "items":json.loads(lotto.items)
    }

from fastapi import Query

@app.get("/lotti")
def lotti(date: str = Query(None), db: Session = Depends(get_db)):
    all_lotti = db.query(Lotto).all()

    if not date:
        # 👉 default = oggi
        today = datetime.now().strftime("%d/%m/%Y")
        filtered = [l for l in all_lotti if l.start_time.startswith(today)]
    else:
        # 👉 filtro per data scelta
        formatted = datetime.strptime(date, "%Y-%m-%d").strftime("%d/%m/%Y")
        filtered = [l for l in all_lotti if l.start_time.startswith(formatted)]

    return filtered

@app.get("/lotti/last")
def get_last_lotto(db: Session = Depends(get_db)):
    last = db.query(Lotto).order_by(Lotto.id.desc()).first()
    if not last:
        return {"codice": ""}
    return {"codice": last.codice}

# ================= START =================
import threading
import webbrowser
import time
import os

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)