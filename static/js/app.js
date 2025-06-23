/********************************************************************
 *  Image-pair preference app – final JS bundle
 *  ---------------------------------------------------------------
 *  Features
 *  • instant swap with pre-fetch           • portrait vs landscape
 *  • 4 vote actions (←, →, ↑, ↓)           • name textbox & cookie
 *  • rich telemetry                        • live leaderboard
 *******************************************************************/

/* ============== Global state ================= */
let prefetchP  = null;   // Promise for the next pair
let current    = null;   // currently displayed pair
let tStart     = 0;      // perf.now when current pair was shown
let hoverL = 0, hoverR = 0;
let hStartL = null, hStartR = null;
let userName  = "anonymous";  // will be filled from backend

/* ============== DOM refs ===================== */
const imgL   = document.getElementById("img-left");
const imgR   = document.getElementById("img-right");
const fbBox  = document.getElementById("feedback");
const nameIn = document.getElementById("user-name");

/* ============== Model map ==================== */
const MODEL_MAP = {
  "claude_v2": {name:"Claude",      color:"rgb(210,125,100)"},
  "out_v2":    {name:"idomoo v0.2", color:"rgb(53,107,246)"},
  "out_v3":    {name:"idomoo v0.3", color:"rgb(53,107,246)"},
  "both_bad":  {name:"Both bad",    color:"#e55"},
  "both_good": {name:"Both good",   color:"#4caf50"}
};

/* ===========================================================
   Utility – fetch JSON helper
=========================================================== */
const jfetch = (url,opt={})=> fetch(url,opt).then(r=>r.json());

/* ===========================================================
   Name handling
=========================================================== */
(async ()=>{
  const {name} = await jfetch("/api/name");
  userName = name || "anonymous";
  nameIn.value = userName;
})();
nameIn.addEventListener("change",async e=>{
  userName = e.target.value.trim().slice(0,40)||"anonymous";
  await fetch("/api/name",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:userName})
  });
  refreshBoard();
});

/* ===========================================================
   Prefetch a pair (returns JSON with decoded images)
=========================================================== */
async function prefetchPair(){
  const p = await jfetch("/api/next");
  const img1 = new Image(), img2 = new Image();
  img1.src = p.left_url;  img2.src = p.right_url;
  await Promise.all([img1.decode?.()||Promise.resolve(),
                     img2.decode?.()||Promise.resolve()]);
  return p;
}

/* ===========================================================
   Display next pair (once prefetch ready)
=========================================================== */
async function showNext(){
  current   = await prefetchP;
  prefetchP = prefetchPair();   // kick off next
  hoverL = hoverR = 0; hStartL = hStartR = null;
  imgL.src = current.left_url;
  imgR.src = current.right_url;
  tStart = performance.now();
}

/* ===========================================================
   Hover helpers
=========================================================== */
function hoverBegin(side){
  const t = performance.now();
  if(side==="left")  hStartL = t;
  if(side==="right") hStartR = t;
}
function hoverEnd(side){
  const t = performance.now();
  if(side==="left" && hStartL!==null){ hoverL+=t-hStartL; hStartL=null; }
  if(side==="right"&& hStartR!==null){ hoverR+=t-hStartR; hStartR=null; }
}

/* ===========================================================
   Feedback
=========================================================== */
function showFeedback(key){
  const info = MODEL_MAP[key] || {name:key,color:"#000"};
  fbBox.textContent = `You picked ${info.name}`;
  fbBox.style.color = info.color;
  setTimeout(()=>{ fbBox.textContent=""; }, 5000);
}

/* ===========================================================
   Vote handler
=========================================================== */
async function vote(choice, method){
  if(!current) return;

  const decision = Math.round(performance.now()-tStart);
  const orient   = matchMedia("(orientation: portrait)").matches ?
                   "portrait":"landscape";

  /* Build payload */
  const body = {
    user_name:      userName,
    image_id:       current.image_id,
    left_folder:    current.left_folder,
    right_folder:   current.right_folder,
    winner:         choice,                 // left/right/both_*
    winner_side:    (choice==="left"||choice==="right")?choice:"none",
    decision_ms:    decision,
    orientation:    orient,
    load_ms:        0,
    input_method:   method,
    hover_left_ms:  Math.round(hoverL),
    hover_right_ms: Math.round(hoverR),
    resolution:     `${innerWidth}x${innerHeight}`
  };

  // POST (await for ordering)
  fetch("/api/vote",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)
  });

  /* feedback key */
  let key = choice;
  if(choice==="left")  key = current.left_folder;
  if(choice==="right") key = current.right_folder;
  showFeedback(key);

  /* next */
  showNext();
}

/* ===========================================================
   Leaderboard polling
=========================================================== */
async function refreshBoard(){
  const list = document.getElementById("leaderboard-list");
  const top = await jfetch("/api/leaderboard");
  list.innerHTML="";
  top.forEach(([n,s])=>{
    const li = document.createElement("li");
    li.textContent = `${n}: ${s}`;
    list.appendChild(li);
  });
}
setInterval(refreshBoard,4000);
refreshBoard();

/* ===========================================================
   Event wiring
=========================================================== */
/* Hover */
imgL.addEventListener("mouseenter",()=>hoverBegin("left"));
imgL.addEventListener("mouseleave",()=>hoverEnd("left"));
imgR.addEventListener("mouseenter",()=>hoverBegin("right"));
imgR.addEventListener("mouseleave",()=>hoverEnd("right"));

/* Clicks */
imgL.addEventListener("click",()=>vote("left","image"));
imgR.addEventListener("click",()=>vote("right","image"));
document.getElementById("btn-left") .addEventListener("click",()=>vote("left","button"));
document.getElementById("btn-right").addEventListener("click",()=>vote("right","button"));
document.getElementById("btn-good") .addEventListener("click",()=>vote("both_good","button"));
document.getElementById("btn-bad")  .addEventListener("click",()=>vote("both_bad","button"));

/* Keyboard */
window.addEventListener("keydown",e=>{
  if(e.key==="ArrowLeft")  vote("left","keyboard");
  if(e.key==="ArrowRight") vote("right","keyboard");
  if(e.key==="ArrowUp")    vote("both_good","keyboard");
  if(e.key==="ArrowDown")  vote("both_bad","keyboard");
});

/* ===========================================================
   Kick-off
=========================================================== */
prefetchP = prefetchPair();
showNext();