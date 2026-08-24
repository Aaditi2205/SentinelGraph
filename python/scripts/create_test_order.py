"""Create a ₹1 Razorpay order in test mode using environment credentials."""
import json
import os
import time
import requests

key_id = os.environ.get("RAZORPAY_KEY_ID", "")
key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
if not key_id.startswith("rzp_test_") or not key_secret:
    raise SystemExit("Set RAZORPAY_KEY_ID (rzp_test_…) and RAZORPAY_KEY_SECRET. Production keys are refused.")
response = requests.post("https://api.razorpay.com/v1/orders", auth=(key_id, key_secret), json={"amount": 100, "currency": "INR", "receipt": f"sg_probe_{int(time.time())}"}, timeout=20)
response.raise_for_status()
order = response.json()
print(json.dumps({key: order.get(key) for key in ("id", "status", "amount", "currency", "receipt")}, indent=2))
