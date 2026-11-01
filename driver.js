const API="/api";let token=localStorage.getItem("okaDriverToken")||"";let driver=null;let offerTimer=null;let pollTimer=null;let locationTimer=null;let currentRide=null;let pollInFlight=false;
const $=id=>document.getElementById(id);
function show(view){$("login-view").classList.toggle("active",view==="login");$("app-view").classList.toggle("active",view==="app")}
function authHeaders(){return token?{Authorization:"Bearer "+token}:{}}
async function api(path,options={}){const headers={"Content-Type":"application/json",...authHeaders(),...(options.headers||{})};const r=await fetch(API+path,{...options,headers});const raw=await r.text().catch(()=>"");let data={};if(raw){try{data=JSON.parse(raw)}catch{data={detail:raw.slice(0,400)}}}if(!r.ok){const detail=typeof data.detail==="string"?data.detail:Array.isArray(data.detail)?data.detail.map(x=>x.msg||JSON.stringify(x)).join("; "):data.message||data.error||"";console.error("Driver API failure",{path,status:r.status,response:data});throw new Error(detail||`HTTP ${r.status}: request failed`)}return data}
function message(text){$("app-message").textContent=text||""}
function setWaiting(title,text){$("waiting-title").textContent=title;$("waiting-text").textContent=text;$("waiting-section").classList.remove("hidden")}
function hideWaiting(){$("waiting-section").classList.add("hidden")}
function renderDriver(){if(!driver)return;$("driver-name").textContent=driver.name||driver.driverId;const online=driver.availabilityStatus==="ONLINE";$("availability-label").textContent=online?"ONLINE":driver.availabilityStatus||"OFFLINE";$("availability-toggle").textContent=online?"Go offline":"Go online";$("availability-toggle").classList.toggle("online",online)}
function renderOffer(offer){if(!offer){$("offer-section").classList.add("hidden");return}$("offer-section").classList.remove("hidden");$("offer-pickup").textContent=offer.pickup||"Pickup not provided";$("offer-destination").textContent=offer.destination||"Ride request";$("offer-destination-2").textContent=offer.destination||"Destination not provided";const expires=offer.dispatchOfferExpiresAt?new Date(offer.dispatchOfferExpiresAt):null;const tick=()=>{if(!expires)return;$("offer-expiry").textContent=Math.max(0,Math.ceil((expires-Date.now())/1000))+"s";if(expires<=Date.now()){clearInterval(offerTimer);renderOffer(null);poll()}};clearInterval(offerTimer);tick();offerTimer=setInterval(tick,1000);$("accept-offer").dataset.id=offer.id||"";$("reject-offer").dataset.id=offer.id||""}
function renderRide(ride){currentRide=ride||null;if(!ride){$("current-section").classList.add("hidden");return}hideWaiting();$("current-section").classList.remove("hidden");$("current-pickup").textContent=ride.pickup||"Pickup not provided";$("current-destination").textContent=ride.destination||"Current ride";$("current-destination-2").textContent=ride.destination||"Destination not provided";$("ride-status").textContent=ride.status||"";$("current-passengers").textContent=ride.passengers?ride.passengers+" passenger(s)":"";const phone=ride.phone||ride.customerPhone||"";$("current-phone").textContent=phone?"Call customer":"";$("current-phone").href=phone?"tel:"+encodeURIComponent(phone):"#";const action=$("ride-action");if(ride.status==="PAID"){action.textContent="Mark picked up";action.disabled=false}else if(ride.status==="PICKED UP"){action.textContent="Complete ride";action.disabled=false}else{action.textContent="Waiting for payment";action.disabled=true}}
async function login(e){e.preventDefault();$("login-message").textContent="";try{const data=await api("/driver/auth/login",{method:"POST",body:JSON.stringify({phone:$("driver-phone").value.trim(),pin:$("driver-pin").value})});token=data.token;localStorage.setItem("okaDriverToken",token);driver=data.driver;show("app");renderDriver();await poll();await loadDriverEarnings()}catch(err){$("login-message").textContent=err.message}}
async function logout(){try{if(driver?.availabilityStatus==="ONLINE")await api("/driver/availability",{method:"PATCH",body:JSON.stringify({availabilityStatus:"OFFLINE"})})}catch{}token="";driver=null;localStorage.removeItem("okaDriverToken");resetDriverEarnings();clearInterval(pollTimer);clearInterval(locationTimer);show("login")}
async function toggleAvailability(){const online=driver.availabilityStatus==="ONLINE";try{const pos=!online&&navigator.geolocation?await new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,reject,{enableHighAccuracy:true,timeout:8000})):null;const body={availabilityStatus:online?"OFFLINE":"ONLINE"};if(pos){body.currentLatitude=pos.coords.latitude;body.currentLongitude=pos.coords.longitude}const data=await api("/driver/availability",{method:"PATCH",body:JSON.stringify(body)});driver.availabilityStatus=data.availabilityStatus;renderDriver();message(online?"You are offline.":"You are online and ready for rides.");await poll()}catch(err){message(err.message)}}
async function updateLocation(){if(!driver||driver.availabilityStatus!=="ONLINE"||!navigator.geolocation)return;navigator.geolocation.getCurrentPosition(async pos=>{try{await api("/driver/availability",{method:"PATCH",body:JSON.stringify({availabilityStatus:"ONLINE",currentLatitude:pos.coords.latitude,currentLongitude:pos.coords.longitude})})}catch{}},()=>{}, {enableHighAccuracy:true,maximumAge:15000,timeout:5000})}
async function poll(){if(!token||pollInFlight||document.hidden)return;pollInFlight=true;try{const s=await api("/driver/auth/session");driver=s.driver;renderDriver();const cur=await api("/driver/rides/current");renderRide(cur.ride);if(!cur.ride&&["ONLINE","BUSY"].includes(driver.availabilityStatus)){const offers=await api("/driver/offers");renderOffer(offers.offers?.[0]||null);if(offers.offers?.length)hideWaiting();else if(driver.availabilityStatus==="BUSY")setWaiting("Checking ride offer","Your status is busy. Looking for an active ride offer.");else setWaiting("Waiting for rides","You\'re online. New ride offers will appear here.")}else if(cur.ride){renderOffer(null)}else setWaiting("You\'re offline","Go online when you\'re ready to receive ride offers.")}catch(err){if(/authentication|session|expired|invalid/i.test(err.message)){logout()}else message(err.message)}finally{pollInFlight=false}}
async function accept(){const id=$("accept-offer").dataset.id;if(!id)return;try{await api("/driver/rides/"+encodeURIComponent(id)+"/accept",{method:"POST"});renderOffer(null);message("Ride accepted.");await poll()}catch(err){message(err.message);await poll()}}
async function reject(){const id=$("reject-offer").dataset.id;if(!id)return;try{await api("/driver/rides/"+encodeURIComponent(id)+"/reject",{method:"POST"});renderOffer(null);await poll()}catch(err){message(err.message)}}
async function rideAction(){if(!currentRide)return;const next=currentRide.status==="PAID"?"PICKED UP":currentRide.status==="PICKED UP"?"COMPLETED":null;if(!next)return;try{await api("/driver/rides/"+encodeURIComponent(currentRide.id)+"/status",{method:"PATCH",body:JSON.stringify({status:next})});message(next==="COMPLETED"?"Ride completed.":"Passenger picked up.");await poll();if(next==="COMPLETED")await loadDriverEarnings()}catch(err){message(err.message)}}
$("login-form").addEventListener("submit",login);$("logout").addEventListener("click",logout);$("availability-toggle").addEventListener("click",toggleAvailability);$("accept-offer").addEventListener("click",accept);$("reject-offer").addEventListener("click",reject);$("ride-action").addEventListener("click",rideAction);
show(token?"app":"login");
if(token){poll();loadDriverEarnings()} 
pollTimer=setInterval(poll,2000);locationTimer=setInterval(updateLocation,30000);document.addEventListener("visibilitychange",()=>{if(!document.hidden)poll()});
if("serviceWorker" in navigator)navigator.serviceWorker.register("/driver-sw.js").catch(()=>{});

/* Read-only earnings. The backend derives driver identity from the signed
   bearer token; a driver cannot request another driver's records. */
let earningsInFlight=false;
const earningsMoney=kobo=>"₦"+(Number(kobo||0)/100).toLocaleString("en-NG",{minimumFractionDigits:2,maximumFractionDigits:2});
function nigeriaBusinessDate(){
  const parts=Object.fromEntries(new Intl.DateTimeFormat("en-GB",{
    timeZone:"Africa/Lagos",year:"numeric",month:"2-digit",day:"2-digit"
  }).formatToParts(new Date()).map(p=>[p.type,p.value]));
  return parts.year+"-"+parts.month+"-"+parts.day;
}
function resetDriverEarnings(){
  ["earnings-count","earnings-total","earnings-pending","earnings-bank-paid","earnings-paystack"].forEach(id=>$(id).textContent="—");
  $("earnings-rides").replaceChildren();
  $("earnings-message").textContent="";
}
function earningsStatus(status){
  if(status==="PAID_OUT")return {label:"Bank payout recorded",style:"settled"};
  if(status==="PROVIDER_SPLIT")return {label:"Paystack split allocated",style:"settled"};
  return {label:"Awaiting bank payout",style:"pending"};
}
function renderDriverEarnings(data){
  const sum=data.summary||{},rides=data.rides||[];
  $("earnings-count").textContent=String(sum.rideCount||0);
  $("earnings-total").textContent=earningsMoney(sum.driverEarningsKobo);
  $("earnings-pending").textContent=earningsMoney(sum.outstandingKobo);
  $("earnings-bank-paid").textContent=earningsMoney(sum.bankPaidKobo);
  $("earnings-paystack").textContent=earningsMoney(sum.paystackAllocatedKobo);
  const list=$("earnings-rides");
  list.replaceChildren();
  $("earnings-message").textContent=rides.length
    ?rides.length+" completed, paid ride(s) for "+data.earningDate+"."
    :"No completed, paid rides recorded for "+data.earningDate+".";
  for(const ride of rides){
    const item=document.createElement("div");
    item.className="earnings-ride";
    const left=document.createElement("div");
    const name=document.createElement("strong");
    name.textContent=ride.rideReference||"Completed ride";
    const detail=document.createElement("small");
    detail.textContent="Fare "+earningsMoney(ride.fareKobo)+" · "+(ride.paymentMethod==="PAYSTACK"?"Paystack":"OPay / offline");
    left.append(name,detail);
    const right=document.createElement("div");
    right.className="amount";
    const value=document.createElement("b");
    value.textContent=earningsMoney(ride.driverEarningKobo);
    const status=earningsStatus(ride.payoutStatus);
    const label=document.createElement("span");
    label.className=status.style;
    label.textContent=status.label;
    right.append(value,label);
    if(ride.payoutStatus==="PAID_OUT"&&ride.payoutReference){
      const ref=document.createElement("small");
      ref.textContent="Transfer: "+ride.payoutReference;
      right.append(ref);
    }
    item.append(left,right);
    list.append(item);
  }
}
async function loadDriverEarnings(){
  if(!token||earningsInFlight||document.hidden)return;
  const date=$("earnings-date").value||nigeriaBusinessDate();
  const currentToken=token;
  earningsInFlight=true;
  $("earnings-refresh").disabled=true;
  $("earnings-message").textContent="Loading earnings…";
  try{
    const data=await api("/driver/earnings?earning_date="+encodeURIComponent(date));
    if(currentToken!==token||$("earnings-date").value!==date)return;
    renderDriverEarnings(data);
  }catch(err){
    if(currentToken===token){
      $("earnings-message").textContent="Unable to load earnings: "+err.message;
    }
  }finally{
    earningsInFlight=false;
    $("earnings-refresh").disabled=false;
    // If the driver selected another date during this request, fetch that
    // selection instead of leaving its loading state indefinitely.
    if(token&&currentToken===token&&$("earnings-date").value!==date&&!document.hidden){
      void loadDriverEarnings();
    }
  }
}
$("earnings-date").value=nigeriaBusinessDate();
$("earnings-date").addEventListener("change",loadDriverEarnings);
$("earnings-refresh").addEventListener("click",loadDriverEarnings);
setInterval(loadDriverEarnings,60000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden)loadDriverEarnings()});
