/* ============================================================
   4-way image variation selection script
   ------------------------------------------------------------
   Behaviour:
   • Shows 4 images in a 2×2 grid.
   • User selects the best by clicking / tapping.
   • Hover scales image slightly.
   • Upon selection the chosen image is highlighted and others fade,
     then next set of images is shown.
   ============================================================ */

/* ------------------- Helpers ------------------- */
const jfetch = (url,opt={}) => fetch(url,opt).then(r=>r.json());

/* ------------------- DOM refs ------------------ */
const grid     = document.getElementById("variation-grid");
const imgs     = Array.from(grid.querySelectorAll("img"));
const fbBox    = document.getElementById("var-feedback");
const btnAllBad = document.getElementById("btn-all-bad");

/* ------------------- State --------------------- */
let prefetchP  = null;   // Promise for the next set
let current    = null;   // current set meta
let tStart     = 0;      // time mark when current set displayed

/* ------------------- Prefetch ------------------ */
async function prefetchSet(){
  const meta = await jfetch("/api/variations/next");
  // Preload 4 images
  const list = meta.variation_urls.map(url => {
    const im = new Image();
    im.src = url;
    return im.decode?.() || Promise.resolve();
  });
  await Promise.all(list);
  return meta;
}

/* ------------------- Display ------------------- */
async function showNext(){
  current   = await prefetchP;
  prefetchP = prefetchSet();

  // Shuffle the order of variations for random grid placement
  const shuffled = current.variation_urls.map((url, i) => ({
    url: url,
    name: current.variation_names[i],
    originalIndex: i
  }));
  
  // Fisher-Yates shuffle
  for(let i = shuffled.length - 1; i > 0; i--){
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }

  imgs.forEach((imgEl,i)=>{
    imgEl.className = "";             // reset classes
    imgEl.src = shuffled[i].url;
    imgEl.dataset.variation = shuffled[i].name;
    imgEl.dataset.originalIndex = shuffled[i].originalIndex;
  });
  fbBox.textContent = "";
  tStart = performance.now();
}

/* ------------------- Vote ---------------------- */
async function vote(index){
  if(!current) return;

  // Visual feedback – highlight selection & fade others
  imgs.forEach((el,i)=>{
    if(i===index){el.classList.add("selected");}
    else{el.classList.add("faded");}
  });

  const decision_ms = Math.round(performance.now()-tStart);
  const clickedImg = imgs[index];
  const body = {
    image_id:          current.image_id,
    winner_variation:  clickedImg.dataset.variation,
    winner_index:      parseInt(clickedImg.dataset.originalIndex),
    grid_position:     index,  // position in the shuffled grid
    decision_ms,
    orientation: matchMedia("(orientation: portrait)").matches ?
                 "portrait":"landscape",
    resolution: `${innerWidth}x${innerHeight}`
  };

  // Fire-and-forget (no await needed for UX) but keep order
  fetch("/api/variations/vote",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)
  });

  /* text feedback */
  fbBox.textContent = `You picked ${clickedImg.dataset.variation}`;

  // Wait a short moment for animation then advance
  setTimeout(showNext, 400);
}

/* ------ All bad handler ------ */
async function voteAllBad(){
  if(!current) return;

  // Fade all images to indicate rejection
  imgs.forEach(el=>el.classList.add("faded"));

  const decision_ms = Math.round(performance.now()-tStart);
  const body = {
    image_id:         current.image_id,
    winner_variation: "all_bad",
    winner_index:     null,
    decision_ms,
    orientation: matchMedia("(orientation: portrait)").matches ?
                 "portrait":"landscape",
    resolution: `${innerWidth}x${innerHeight}`
  };

  fetch("/api/variations/vote",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)
  });

  fbBox.textContent = "You marked all variations bad";
  setTimeout(showNext, 400);
}

/* ------------------- Event wiring -------------- */
imgs.forEach((imgEl,i)=>{
  imgEl.addEventListener("click",()=>vote(i));
});

btnAllBad.addEventListener("click",voteAllBad);

/* Keyboard handler */
window.addEventListener("keydown",e=>{
  if(e.key===" " || e.code==="Space"){
    e.preventDefault(); // prevent page scroll
    voteAllBad();
  }
});

/* ------------------- Kick-off ------------------ */
prefetchP = prefetchSet();
showNext(); 