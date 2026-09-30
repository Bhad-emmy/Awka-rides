
let okaMap=null,okaMapMarker=null;
function initOkaMap(){const el=document.getElementById("oka-map");if(!el||typeof L==="undefined")return;if(okaMap)return;const center=[6.2105,7.0677];okaMap=L.map(el,{zoomControl:true,attributionControl:true}).setView(center,13);L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}).addTo(okaMap);okaMapMarker=L.marker(center).addTo(okaMap).bindPopup("Awka, Anambra").openPopup();setTimeout(()=>okaMap.invalidateSize(),100);if(navigator.geolocation){navigator.geolocation.getCurrentPosition(pos=>{const p=[pos.coords.latitude,pos.coords.longitude];okaMap.setView(p,14);okaMapMarker.setLatLng(p).bindPopup("Your current location").openPopup()},()=>{});}}
const SUPABASE_URL="https://baoqyhhyenimfhkrwspn.supabase.co";const SUPPORT_PHONE="09064636239";const SUPABASE_KEY="sb_publishable_o20CnPYPZC6czgwTKIfAVA_M4XFA0Dy";const supabaseClient=window.supabase?.createClient(SUPABASE_URL,SUPABASE_KEY);const API_BASE="/api";const screens=[...document.querySelectorAll(".screen")];const nav=[...document.querySelectorAll(".nav-item")];let currentSession=null;let csrfToken=null;function go(name){screens.forEach(s=>s.classList.toggle("active",s.dataset.screen===name));nav.forEach(n=>n.classList.toggle("active",n.dataset.nav===name));const bottom=document.querySelector(".bottom-nav");const noNav=["splash","login","signup","profile"].includes(name);if(bottom)bottom.hidden=noNav;document.querySelector(".app-shell")?.classList.toggle("splash-mode",noNav);document.documentElement.classList.toggle("auth-lock",["splash","login","signup"].includes(name));document.body.classList.toggle("auth-lock",["splash","login","signup"].includes(name));window.scrollTo(0,0);if(name==="home")setTimeout(initOkaMap,50);if(name==="activity")loadRides();if(name==="account")renderAccount();if(name==="previous-rides")loadPreviousRides();if(name==="profile")renderProfile()}async function api(path,options={}){const method=String(options.method||"GET").toUpperCase();const headers={"Content-Type":"application/json",...(options.headers||{})};if(!["GET","HEAD","OPTIONS"].includes(method)&&csrfToken)headers["X-CSRF-Token"]=csrfToken;const r=await fetch(API_BASE+path,{credentials:"include",...options,headers});const data=await r.json().catch(()=>({}));if(!r.ok){const detail=data?.detail;if(Array.isArray(detail))throw new Error(detail.map(x=>x?.msg||x?.message||String(x)).join(" "));throw new Error(detail?.message||detail||"Request failed")}return data}function renderAccount(){const name=document.querySelector("#profile-name"),email=document.querySelector("#profile-email"),auth=document.querySelector("#account-auth-action"),profile=document.querySelector("#profile-action"),logout=document.querySelector("#logout-btn");if(currentSession){name.textContent=(currentSession.full_name||currentSession.fullName||"Customer").replace(/\s+/g," ").trim();email.textContent=currentSession.email||currentSession.phone||"";auth.hidden=true;profile.hidden=false;logout.hidden=false}else{name.textContent="Guest";email.textContent="Sign in to manage your rides";auth.hidden=false;profile.hidden=true;logout.hidden=true}}function renderProfile(){
  const name=document.querySelector("#profile-full-name");
  const phone=document.querySelector("#profile-phone");
  const email=document.querySelector("#profile-email-input");
  if(!name||!phone||!email)return;
  if(!currentSession){name.value="";phone.value="";email.value="";return}
  name.value=(currentSession.full_name||currentSession.fullName||"").replace(/\s+/g," ").trim();
  phone.value=currentSession.phone||"";
  email.value=currentSession.email||"";
}

async function loadSession(){try{const data=await api("/customer/auth/session");currentSession=data?.customer||null;if(data?.csrfToken)csrfToken=data.csrfToken;else if(!currentSession)csrfToken=null}catch{currentSession=null;csrfToken=null}renderAccount();return currentSession}document.addEventListener("click",e=>{if(e.target.closest("#app-login-form,#app-signup-form"))return;const n=e.target.closest("[data-nav]");if(n)return go(n.dataset.nav);const promo=e.target.closest("#promo-card");if(promo){e.preventDefault();e.stopPropagation();return}const brand=e.target.closest(".home-brand");if(brand){e.preventDefault();e.stopPropagation();return go("home")}const b=e.target.closest("[data-go]");if(b)return go(b.dataset.go);if(e.target.closest("#home-account"))return go("account");if(e.target.closest("#splash-login"))return go("login");if(e.target.closest("#splash-signup"))return go("signup");if(e.target.closest("#continue-session"))return go("home");if(e.target.closest("#show-signup"))return go("signup");if(e.target.closest("#account-auth-action"))return go("login");if(e.target.closest("#call-dispatch"))window.location.href="tel:"+encodeURIComponent(SUPPORT_PHONE);if(e.target.closest("#support-action"))window.location.href="tel:"+encodeURIComponent(SUPPORT_PHONE)});let loginSubmitArmed=false;
document.querySelector("#app-login-form")?.addEventListener("submit",async e=>{
  e.preventDefault();
  const m=document.querySelector("#app-login-message");
  m.textContent="Signing in...";
  try{
    await api("/customer/auth/login",{method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))});
    await loadSession();
    localStorage.setItem("okaSignedIn","1");
    go("home");
  }catch(err){m.textContent=err.message}
});
document.querySelector("#app-signup-form")?.addEventListener("submit",async e=>{e.preventDefault();const m=document.querySelector("#app-signup-message");m.textContent="Creating account...";try{await api("/customer/auth/signup",{method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))});await loadSession();localStorage.setItem("okaSignedIn","1");go("home")}catch(err){m.textContent=err.message}});document.querySelector("#app-profile-form")?.addEventListener("submit",async e=>{
  e.preventDefault();
  if(!currentSession){go("login");return}
  const m=document.querySelector("#app-profile-message");
  const form=e.currentTarget;
  const v=Object.fromEntries(new FormData(form));
  m.textContent="Saving changes...";
  try{
    const data=await api("/customer/profile",{method:"PATCH",body:JSON.stringify({fullName:v.fullName,phone:v.phone,reauth:v["current"+"Password"]||""})});
    currentSession=data?.customer||currentSession;
    renderAccount();
    renderProfile();
    m.textContent="Profile updated.";
    setTimeout(()=>go("account"),500);
  }catch(err){m.textContent=err.message}
});

document.querySelector("#app-booking-form")?.addEventListener("submit",async e=>{e.preventDefault();const form=e.currentTarget;const m=document.querySelector("#app-booking-message"),v=Object.fromEntries(new FormData(form));m.textContent="Requesting your ride...";try{const s=currentSession||await loadSession();const payload={name:s?.full_name||s?.fullName||"",phone:v.phone,email:s?.email||"",pickup:v.pickup,destination:v.destination,pickupTiming:v.pickupTiming,passengers:String(v.passengers),notes:v.notes,vehicle:"Keke"};const data=await api("/rides",{method:"POST",body:JSON.stringify(payload)});m.textContent="Ride requested: "+(data.reference||"created");form.reset();setTimeout(()=>go("activity"),700)}catch(err){m.textContent=err.message}});function escapeHtml(v){return String(v??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
let ridePollTimer=null;
let activeRideReference=null;
let activeRideStatus=null;

function stopRidePolling(){if(ridePollTimer){clearInterval(ridePollTimer);ridePollTimer=null}}
function startRidePolling(reference){activeRideReference=reference;stopRidePolling();ridePollTimer=setInterval(async()=>{try{const r=await api("/customer/rides/"+encodeURIComponent(reference));activeRideStatus=r.status;if(["COMPLETED","CANCELLED"].includes(r.status)){stopRidePolling()}await openRide(reference,true)}catch{}} ,5000)}

async function openRide(reference,silent=false){
  const detail=document.querySelector("#ride-detail");
  if(!detail)return;
  detail.hidden=false;
  activeRideReference=reference;
  if(!silent)startRidePolling(reference);
  if(!silent)detail.innerHTML='<div class="ride-detail-loading">Loading ride details...</div>';
  try{
    const r=await api("/customer/rides/"+encodeURIComponent(reference));
    const assigned=!!r.driverAssigned;
    detail.innerHTML='<div class="ride-detail-head"><div><small>Ride</small><strong>'+escapeHtml(r.reference||reference)+'</strong></div><button class="round-icon" id="close-ride-detail" aria-label="Close">×</button></div>'+
      '<div class="ride-status-large">'+escapeHtml(r.status||"REQUESTED")+'</div>'+
      '<div class="ride-detail-grid"><div><small>Fare</small><b>₦'+escapeHtml(r.fare??0)+'</b></div><div><small>Payment</small><b>'+escapeHtml(r.paymentStatus||"PENDING")+'</b></div></div>'+
      (assigned?
        '<div class="driver-panel"><div class="driver-panel-title">Your driver</div><h3>'+escapeHtml(r.driverName||"Driver assigned")+'</h3><p>'+escapeHtml(r.driverVehicle||"Keke")+(r.driverPlateNumber?' • '+escapeHtml(r.driverPlateNumber):"")+'</p>'+
        (r.driverPhone?'<a class="driver-call" href="tel:'+encodeURIComponent(r.driverPhone)+'">Call driver</a>':"<p class=\"driver-muted\">Driver phone number not available.</p>")+
        '</div>':
        '<div class="driver-panel pending"><div class="driver-panel-title">Driver</div><p>Your ride has been requested. The dispatcher will assign a driver here.</p></div>');
    document.querySelector("#close-ride-detail")?.addEventListener("click",()=>{detail.hidden=true;stopRidePolling();activeRideReference=null});
  }catch(err){detail.innerHTML='<div class="ride-detail-loading">'+escapeHtml(err.message)+'</div>'}
}
async function loadPreviousRides(){
  const list=document.querySelector("#previous-rides-list");
  if(!list)return;
  if(!currentSession){list.innerHTML='<div class="empty"><span class="empty-icon">🛺</span><h2>Sign in to see previous rides</h2><p>Your completed and cancelled trips are saved to your account.</p><button class="secondary" data-go="login">Log in</button></div>';return}
  try{
    const data=await api("/customer/rides");
    const rides=(Array.isArray(data?.rides)?data.rides:[]).filter(r=>["COMPLETED","CANCELLED"].includes(String(r.status||"").toUpperCase()));
    if(!rides.length){list.innerHTML='<div class="empty"><span class="empty-icon">🛺</span><h2>No previous rides</h2><p>Your completed and cancelled rides will appear here.</p></div>';return}
    list.innerHTML=rides.map(r=>{
      const when=r.createdAt?new Date(r.createdAt).toLocaleDateString("en-NG",{day:"numeric",month:"short",year:"numeric"}):"";
      return '<div class="previous-ride-card">'+
        '<div class="previous-ride-main"><div class="ride-thumb">🛺</div><div><h3>'+escapeHtml(r.destination||"Ọka-Rides trip")+'</h3><small>'+escapeHtml(r.pickup||"")+'</small><small>'+escapeHtml(when)+'</small><div class="status">'+escapeHtml(r.status||"COMPLETED")+'</div></div><div class="fare">₦'+escapeHtml(r.fare??0)+'</div></div>'+
        '<button class="secondary wide previous-rebook" type="button" data-rebook-reference="'+escapeHtml(r.reference||"")+'">Rebook</button>'+
        '</div>';
    }).join("");
    list.querySelectorAll(".previous-rebook").forEach(button=>button.addEventListener("click",()=>rebookRide(button.dataset.rebookReference)));
  }catch(err){list.innerHTML='<div class="empty"><h2>Could not load previous rides</h2><p>'+escapeHtml(err.message)+'</p></div>'}
}

async function rebookRide(reference){
  try{
    const data=await api("/customer/rides");
    const source=(Array.isArray(data?.rides)?data.rides:[]).find(r=>r.reference===reference);
    if(!source)throw new Error("Previous ride could not be found.");
    const form=document.querySelector("#app-booking-form");
    if(!form)throw new Error("Booking form is unavailable.");
    const set=(name,value)=>{const field=form.elements[name];if(field&&value!==undefined&&value!==null)field.value=String(value)};
    set("pickup",source.pickup);
    set("destination",source.destination);
    set("pickupTiming",source.pickupTiming||"Now");
    set("passengers",source.passengers||"1");
    set("phone",source.phone||currentSession?.phone||"");
    set("notes",source.notes||"");
    const message=document.querySelector("#app-booking-message");
    if(message)message.textContent="Previous ride loaded. Review the details and request your new ride.";
    go("book");
  }catch(err){
    const list=document.querySelector("#previous-rides-list");
    if(list)list.insertAdjacentHTML("afterbegin",'<p class="form-error">'+escapeHtml(err.message)+'</p>');
  }
}

async function loadRides(){
  const list=document.querySelector("#ride-list");
  if(!currentSession){list.innerHTML='<div class="empty"><span class="empty-icon">🛺</span><h2>Sign in to see your rides</h2><p>Your activity is saved to your Ọka-Rides account.</p><button class="secondary" data-go="login">Log in</button></div>';return}
  try{
    const data=await api("/customer/rides");const rides=Array.isArray(data?.rides)?data.rides:[];
    if(!rides.length){list.innerHTML='<div class="empty"><span class="empty-icon">🛺</span><h2>No rides yet</h2><p>Your active and completed rides will appear here.</p></div>';return}
    list.innerHTML=rides.map(r=>'<button class="ride-card" data-ride-reference="'+escapeHtml(r.reference||"")+'"><div class="ride-thumb">🛺</div><div><h3>'+escapeHtml(r.destination||r.pickup||"Ọka-Rides trip")+'</h3><small>'+escapeHtml(r.pickup||"")+'</small><div class="status">'+escapeHtml(r.status||"REQUESTED")+'</div></div><div class="fare">₦'+escapeHtml(r.fare??0)+'</div></button>').join("");
    list.querySelectorAll("[data-ride-reference]").forEach(card=>card.addEventListener("click",()=>openRide(card.dataset.rideReference)));
  }catch{list.innerHTML='<div class="empty"><h2>Could not load activity</h2><p>Pull again in a moment.</p></div>'}
}document.querySelector("#refresh-rides")?.addEventListener("click",loadRides);document.querySelector("#refresh-previous-rides")?.addEventListener("click",loadPreviousRides);document.querySelector("#logout-btn")?.addEventListener("click",async()=>{try{await api("/customer/auth/logout",{method:"POST"})}catch{}currentSession=null;csrfToken=null;localStorage.removeItem("okaSignedIn");renderAccount();go("splash")});const continueButton=document.querySelector("#continue-session"),sessionNote=document.querySelector("#session-note");async function restoreCustomerSession(){let s=null;for(let attempt=0;attempt<3;attempt++){try{s=await loadSession();if(s)break}catch{}if(!s&&attempt<2)await new Promise(r=>setTimeout(r,500))}if(s){localStorage.setItem("okaSignedIn","1");continueButton.hidden=false;sessionNote.textContent="Signed in as "+(s.email||s.fullName||"customer")}else{localStorage.removeItem("okaSignedIn");continueButton.hidden=true;sessionNote.textContent=""}return s}document.querySelector("#google-login")?.addEventListener("click",async()=>{if(!supabaseClient)return;const b=document.querySelector("#google-login");b.disabled=true;sessionNote.textContent="Opening Google sign-in...";const {error}=await supabaseClient.auth.signInWithOAuth({provider:"google",options:{redirectTo:window.location.origin+"/"}});if(error){sessionNote.textContent=error.message;b.disabled=false}});async function finishGoogleSignIn(){if(!supabaseClient)return;const {data}=await supabaseClient.auth.getSession();const session=data?.session;if(!session)return;let phone=localStorage.getItem("okaGooglePhone")||"";let response=await fetch(API_BASE+"/customer/auth/google",{method:"POST",headers:{"Content-Type":"application/json"},credentials:"include",body:JSON.stringify({accessToken:session.access_token,phone})});if(response.status===422){phone=window.prompt("Enter your phone number to finish your Ọka-Rides account:","09064636239")||"";if(phone)response=await fetch(API_BASE+"/customer/auth/google",{method:"POST",headers:{"Content-Type":"application/json"},credentials:"include",body:JSON.stringify({accessToken:session.access_token,phone})})}const result=await response.json().catch(()=>({}));if(response.ok){localStorage.removeItem("okaGooglePhone");await supabaseClient.auth.signOut();await restoreCustomerSession();go("home")}else sessionNote.textContent=result.detail||"Google sign-in could not be completed."}(async()=>{const s=await restoreCustomerSession();await finishGoogleSignIn();if(!currentSession&&!s&&localStorage.getItem("okaSignedIn")!=="1")go("splash");else if(currentSession&&document.querySelector(".screen.active")?.dataset.screen==="splash")go("home")})();