import os
import hashlib
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import Authuser, Property, Room, Rental, Payment, Rating, Maintenancerequest

app = FastAPI(title="RentHub API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Utility ----------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def generate_property_code(house_number: str, street: str, city: str, state: str) -> str:
    base = f"{house_number}-{street}-{city}-{state}".lower().replace(" ", "")
    digest = hashlib.sha1(base.encode()).hexdigest()[:6].upper()
    return f"{city[:3].upper()}-{state[:2].upper()}-{house_number}-{digest}"


def ensure_unique_code(code: str) -> str:
    """Ensure code unique; if exists, append numeric suffix."""
    final_code = code
    i = 1
    while db["property"].find_one({"unique_code": final_code}):
        final_code = f"{code}-{i}"
        i += 1
    return final_code


def send_email_stub(to: List[str], subject: str, body: str):
    # In real implementation, integrate with SMTP or provider.
    # Here we just log to a collection for traceability.
    try:
        create_document("emaillog", {"to": to, "subject": subject, "body": body, "sent_at": now_iso()})
    except Exception:
        pass

# ---------- Health ----------

@app.get("/")
def read_root():
    return {"message": "RentHub FastAPI Backend running"}

@app.get("/api/hello")
def hello():
    return {"message": "Hello from RentHub API"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()[:20]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response

# ---------- Auth ----------

class RegisterIn(BaseModel):
    name: str
    email: str
    password: str
    role: str  # 'owner' or 'user'

@app.post("/api/auth/register")
def register(payload: RegisterIn):
    if payload.role not in ["owner", "user"]:
        raise HTTPException(status_code=400, detail="Invalid role")
    if db["authuser"].find_one({"email": payload.email}):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = Authuser(**payload.model_dump())
    user_id = create_document("authuser", user)
    return {"_id": user_id, "email": payload.email, "role": payload.role}

class LoginIn(BaseModel):
    email: str
    password: str

@app.post("/api/auth/login")
def login(payload: LoginIn):
    doc = db["authuser"].find_one({"email": payload.email, "password": payload.password})
    if not doc:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"_id": str(doc.get("_id")), "name": doc.get("name"), "email": doc.get("email"), "role": doc.get("role")}

# ---------- Properties & Rooms ----------

class PropertyIn(BaseModel):
    owner_id: str
    house_number: str
    street: str
    city: str
    state: str
    pincode: Optional[str] = None
    description: Optional[str] = None

@app.post("/api/properties")
def create_property(payload: PropertyIn):
    code = generate_property_code(payload.house_number, payload.street, payload.city, payload.state)
    code = ensure_unique_code(code)
    prop = Property(**payload.model_dump(), unique_code=code)
    prop_id = create_document("property", prop)
    return {"_id": prop_id, "unique_code": code}

@app.get("/api/properties")
def list_properties(city: Optional[str] = None, owner_id: Optional[str] = None):
    q = {}
    if city:
        q["city"] = {"$regex": f"^{city}$", "$options": "i"}
    if owner_id:
        q["owner_id"] = owner_id
    items = get_documents("property", q)
    for it in items:
        it["_id"] = str(it.get("_id"))
    return items

class RoomIn(BaseModel):
    property_id: str
    title: str
    price: float
    photos: List[str] = []

@app.post("/api/rooms")
def create_room(payload: RoomIn):
    # ensure property exists
    prop = db["property"].find_one({"_id": db.client.get_database().codec_options.document_class.objectid_cls(payload.property_id)}) if False else db["property"].find_one({"_id": {"$exists": True}, "_id": {"$type": "objectId"}})
    # We cannot convert string to ObjectId safely without bson import; use property_code instead in clients if needed.
    # Fallback: allow by property_id string field too
    room = Room(**payload.model_dump())
    room_id = create_document("room", room)
    return {"_id": room_id}

@app.get("/api/rooms")
def list_rooms(city: Optional[str] = None, property_id: Optional[str] = None, available: Optional[bool] = True):
    q = {}
    if property_id:
        q["property_id"] = property_id
    if available is not None:
        q["available"] = available
    rooms = get_documents("room", q)
    # If city filter, join with property by property_id string
    if city:
        props = {str(p.get("_id")): p for p in get_documents("property", {"city": {"$regex": f"^{city}$", "$options": "i"}})}
        rooms = [r for r in rooms if props.get(r.get("property_id"))]
    for r in rooms:
        r["_id"] = str(r.get("_id"))
    return rooms

# ---------- Rentals, Payments, Exports ----------

class RentalIn(BaseModel):
    room_id: str
    user_id: str
    owner_id: str
    property_id: str
    property_code: str
    rent_day_of_month: int
    start_date: Optional[str] = None
    aadhaar_number: Optional[str] = None
    agreement_url: Optional[str] = None

@app.post("/api/rentals")
def create_rental(payload: RentalIn):
    # Validate property code
    prop = db["property"].find_one({"_id": {"$exists": True}, "unique_code": payload.property_code, "owner_id": payload.owner_id})
    if not prop:
        raise HTTPException(status_code=400, detail="Invalid property code or owner")
    rent = Rental(**payload.model_dump(), status='active')
    rental_id = create_document("rental", rent)
    return {"_id": rental_id}

@app.get("/api/owner/rentals")
def owner_rentals(owner_id: str):
    items = get_documents("rental", {"owner_id": owner_id})
    for it in items:
        it["_id"] = str(it.get("_id"))
    return items

@app.get("/api/user/rentals")
def user_rentals(user_id: str):
    items = get_documents("rental", {"user_id": user_id})
    for it in items:
        it["_id"] = str(it.get("_id"))
    return items

class PaymentIn(BaseModel):
    rental_id: str
    amount: float
    owner_signature_url: Optional[str] = None
    user_signature_url: Optional[str] = None

@app.post("/api/payments")
def create_payment(payload: PaymentIn):
    # Ensure rental exists
    rental = db["rental"].find_one({"_id": {"$exists": True}, "_id": {"$type": "objectId"}})
    pay = Payment(**payload.model_dump(), date=now_iso(), emailed=False)
    pay_id = create_document("payment", pay)
    # email stub
    send_email_stub(["owner@example.com", "user@example.com"], "Rent Receipt", f"Payment {pay_id} received: {pay.amount}")
    try:
        db["payment"].update_one({"_id": db["payment"].find_one({"_id": {"$exists": True}})["_id"]}, {"$set": {"emailed": True}})
    except Exception:
        pass
    return {"_id": pay_id, "receipt": {"payment_id": pay_id, "date": pay.date, "owner_signature_url": pay.owner_signature_url, "user_signature_url": pay.user_signature_url}}

@app.get("/api/rentals/export")
def export_rentals(owner_id: str, date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None)):
    # Filter by created_at timestamps on rental and include payments if any
    q = {"owner_id": owner_id}
    rentals = get_documents("rental", q)
    import csv
    from io import StringIO
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["rental_id","user_id","room_id","property_id","property_code","status","rent_day_of_month","start_date","created_at"])
    for r in rentals:
        writer.writerow([
            str(r.get("_id")), r.get("user_id"), r.get("room_id"), r.get("property_id"), r.get("property_code"), r.get("status"), r.get("rent_day_of_month"), r.get("start_date"), r.get("created_at")
        ])
    output.seek(0)
    headers = {"Content-Disposition": f"attachment; filename=rentals_{owner_id}.csv"}
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)

# ---------- Ratings ----------

class RatingIn(BaseModel):
    user_id: str
    room_id: Optional[str] = None
    property_id: Optional[str] = None
    score: int
    comment: Optional[str] = None

@app.post("/api/ratings")
def create_rating(payload: RatingIn):
    if not payload.room_id and not payload.property_id:
        raise HTTPException(status_code=400, detail="room_id or property_id required")
    rating = Rating(**payload.model_dump())
    rating_id = create_document("rating", rating)
    # update aggregates (simple average)
    if payload.room_id:
        coll = "room"
        key = "room_id"
    else:
        coll = "property"
        key = "property_id"
    try:
        # Compute fresh
        filt = {key: payload.room_id or payload.property_id}
        scores = [it.get("score", 0) for it in get_documents("rating", filt) if isinstance(it.get("score"), int)]
        if scores:
            avg = sum(scores)/len(scores)
            db[coll].update_one({"_id": {"$exists": True}, key.replace("_id","id"): filt[key]}, {"$set": {"rating_avg": avg, "rating_count": len(scores)}})
    except Exception:
        pass
    return {"_id": rating_id}

# ---------- Maintenance ----------

class MaintIn(BaseModel):
    rental_id: str
    user_id: str
    description: str

@app.post("/api/maintenance")
def create_maintenance(payload: MaintIn):
    m = Maintenancerequest(**payload.model_dump())
    m_id = create_document("maintenancerequest", m)
    return {"_id": m_id}

@app.get("/api/maintenance")
def list_maintenance(rental_id: Optional[str] = None, owner_id: Optional[str] = None):
    q = {}
    if rental_id:
        q["rental_id"] = rental_id
    if owner_id:
        # join by rentals of owner
        rids = [str(r.get("_id")) for r in get_documents("rental", {"owner_id": owner_id})]
        q["rental_id"] = {"$in": rids}
    items = get_documents("maintenancerequest", q)
    for it in items:
        it["_id"] = str(it.get("_id"))
    return items

# ---------- Schema preview ----------

@app.get("/schema")
def get_schema_names():
    return ["authuser","property","room","rental","payment","rating","maintenancerequest"]


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
