"""
api/routes/payments.py — Razorpay subscription handling.

Flow:
    1. Frontend calls POST /payments/create-order
    2. We create a Razorpay order, return order_id
    3. Frontend shows Razorpay checkout popup
    4. User pays
    5. Razorpay sends webhook to POST /payments/webhook
    6. We verify signature, activate subscription in DB
"""

import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from api.deps import get_current_user
from config import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, PLANS, APP_NAME
import db

log = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ─────────────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    plan: str   # "starter" | "farmer" | "pro"


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/plans")
def list_plans():
    """Return all available subscription plans."""
    return PLANS


@router.post("/create-order")
def create_order(body: CreateOrderRequest, request: Request):
    """
    Step 1 — Create a Razorpay order.
    Frontend uses the returned order_id to open checkout popup.
    """
    user = get_current_user(request)

    plan = PLANS.get(body.plan)
    if not plan:
        raise HTTPException(status_code=400, detail="Invalid plan")

    try:
        import razorpay
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

        order = client.order.create({
            "amount":   plan["price_inr"] * 100,   # Razorpay uses paise
            "currency": "INR",
            "notes": {
                "user_id":  str(user["id"]),
                "plan":     body.plan,
                "app_name": APP_NAME,
            },
        })

        log.info(f"[payments] Order created: {order['id']} for user {user['id']} plan={body.plan}")

        return {
            "order_id":   order["id"],
            "amount":     order["amount"],
            "currency":   order["currency"],
            "key_id":     RAZORPAY_KEY_ID,
            "plan_name":  plan["name"],
            "user_name":  user["name"],
            "user_email": user["email"],
        }

    except Exception as exc:
        log.error(f"[payments] Order creation failed: {exc}")
        raise HTTPException(status_code=500, detail="Payment setup failed")


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Step 2 — Razorpay calls this after payment succeeds.
    We verify the signature and activate the subscription.
    """
    body_bytes = await request.body()
    signature  = request.headers.get("x-razorpay-signature", "")

    # Verify webhook signature (security — ensure it's from Razorpay)
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        log.warning("[payments] Webhook signature mismatch — rejected")
        raise HTTPException(status_code=400, detail="Invalid signature")

    import json
    payload = json.loads(body_bytes)
    event   = payload.get("event", "")

    if event == "payment.captured":
        payment = payload["payload"]["payment"]["entity"]
        notes   = payment.get("notes", {})
        user_id = notes.get("user_id")
        plan    = notes.get("plan", "starter")

        if user_id:
            ph = db._ph()
            with db.get_conn() as conn:
                if db.USE_POSTGRES:
                    cur = conn.cursor()
                    cur.execute(
                        f"UPDATE users SET subscription_status='active', plan={ph} WHERE id={ph}",
                        (plan, int(user_id)),
                    )
                    conn.commit()
                else:
                    conn.execute(
                        f"UPDATE users SET subscription_status='active', plan={ph} WHERE id={ph}",
                        (plan, int(user_id)),
                    )
            log.info(f"[payments] Activated plan={plan} for user_id={user_id}")

    return {"status": "ok"}
