/********************************************************************
 * Two-way image variation selection script
 * ---------------------------------------------------------------
 * Shows two images side-by-side, each from a different variation
 * folder. User can vote Left (←), Right (→), Both good (↑), Both
 * bad (↓) or skip (Space). The vote is POSTed to
 * /api/variations/vote and a new pair is shown instantly.
 *******************************************************************/

/* ------------------- Helpers ------------------- */
const jfetch = (url,opt={})=> fetch(url,opt).then(r=>r.json());

/* ------------------- DOM refs ------------------ */
const imgL = document.getElementById("img-left");
const imgR = document.getElementById("img-right");
const fbBox = document.getElementById("var-feedback");

/* ------------------- State --------------------- */
let prefetchP = null;    // Promise with next pair
let current   = null;    // currently shown pair (meta)
let tStart    = 0;       // perf.now when pair displayed

/* ------------------- Prefetch ------------------ */
async function prefetchPair(){
  const meta = await jfetch("/api/variations/next");
  const im1 = new Image(); im1.src = meta.left_url;
  const im2 = new Image(); im2.src = meta.right_url;
  await Promise.all([im1.decode?.()||Promise.resolve(),
                     im2.decode?.()||Promise.resolve()]);
  return meta;
}

/* ------------------- Display ------------------- */
async function showNext(){
  current   = await prefetchP;
  prefetchP = prefetchPair();

  imgL.src = current.left_url;
  imgR.src = current.right_url;
  imgL.className = "";
  imgR.className = "";
  fbBox.textContent = "";

  tStart = performance.now();
}

/* ------------------- Vote ---------------------- */
async function vote(choice){
  if(!current) return;

  // Visual cues
  if(choice==="left"){
    imgL.classList.add("selected");
    imgR.classList.add("faded");
  }
  if(choice==="right"){
    imgR.classList.add("selected");
    imgL.classList.add("faded");
  }
  if(choice==="both_bad"){
    imgL.classList.add("faded");
    imgR.classList.add("faded");
  }
  if(choice==="both_good"){
    // highlight both good
    imgL.classList.add("selected");
    imgR.classList.add("selected");
  }

  const decision_ms = Math.round(performance.now()-tStart);
  const orient = matchMedia("(orientation: portrait)").matches ?
                 "portrait" : "landscape";

  // Build payload
  const body = {
    image_id: current.image_id,
    left_variation:  current.left_variation,
    right_variation: current.right_variation,
    winner_choice: choice,   // left/right/both_good/both_bad
    decision_ms,
    orientation: orient,
    resolution: `${innerWidth}x${innerHeight}`
  };

  // Only send if not skip
  if(choice!=="skip"){
    fetch("/api/variations/vote",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body)
    });
  }

  // Feedback text (omit for skip)
  if(choice!=="skip"){
    let msg="";
    switch(choice){
      case "left": msg = `You picked ${current.left_variation}`; break;
      case "right": msg = `You picked ${current.right_variation}`; break;
      case "both_good": msg = "You marked both good"; break;
      case "both_bad": msg = "You marked both bad"; break;
    }
    fbBox.textContent = msg;
  }

  // Delay just a little for feedback
  setTimeout(showNext, 300);
}

/* ------------------- Event wiring -------------- */
imgL.addEventListener("click", ()=> vote("left"));
imgR.addEventListener("click", ()=> vote("right"));

document.getElementById("btn-left").addEventListener("click", ()=>vote("left"));
document.getElementById("btn-right").addEventListener("click", ()=>vote("right"));
document.getElementById("btn-good").addEventListener("click", ()=>vote("both_good"));
document.getElementById("btn-bad").addEventListener("click", ()=>vote("both_bad"));

/* Keyboard handling */
window.addEventListener("keydown", e=>{
  switch(e.key){
    case "ArrowLeft":  vote("left"); break;
    case "ArrowRight": vote("right"); break;
    case "ArrowUp":    vote("both_good"); break;
    case "ArrowDown":  vote("both_bad"); break;
    default: break;
  }
  if(e.code === "Space"){
    e.preventDefault();
    vote("skip");
  }
});

/* ------------------- Kick off ------------------ */
prefetchP = prefetchPair();
showNext(); 