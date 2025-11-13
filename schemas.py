"""
Database Schemas for RentHub

Each Pydantic model maps to a MongoDB collection (lowercase of class name).
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class Authuser(BaseModel):
    name: str
    email: str
    password: str
    role: Literal['owner','user']
    is_active: bool = True

class Property(BaseModel):
    owner_id: str
    house_number: str
    street: str
    city: str
    state: str
    pincode: Optional[str] = None
    description: Optional[str] = None
    unique_code: str
    rating_avg: float = 0.0
    rating_count: int = 0

class Room(BaseModel):
    property_id: str
    title: str
    price: float
    photos: List[str] = []
    available: bool = True
    rating_avg: float = 0.0
    rating_count: int = 0

class Rental(BaseModel):
    room_id: str
    user_id: str
    owner_id: str
    property_id: str
    property_code: str
    rent_day_of_month: int = Field(ge=1, le=28)
    start_date: Optional[str] = None  # ISO date
    status: Literal['active','ended'] = 'active'
    aadhaar_number: Optional[str] = None
    agreement_url: Optional[str] = None

class Payment(BaseModel):
    rental_id: str
    amount: float
    date: Optional[str] = None  # ISO datetime (client or server)
    owner_signature_url: Optional[str] = None
    user_signature_url: Optional[str] = None
    emailed: bool = False

class Rating(BaseModel):
    user_id: str
    room_id: Optional[str] = None
    property_id: Optional[str] = None
    score: int = Field(ge=1, le=5)
    comment: Optional[str] = None

class Maintenancerequest(BaseModel):
    rental_id: str
    user_id: str
    description: str
    status: Literal['open','in_progress','closed'] = 'open'
