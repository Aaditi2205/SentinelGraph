"""Send an official-shaped, correctly signed test webhook to localhost."""
import argparse
import hashlib
import hmac
import json
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--secret", required=True)
parser.add_argument("--event-id", default="evt_sg_cli_001")
args = parser.parse_args()
payload = {"event": "payment.captured", "created_at": 1, "payload": {"payment": {"entity": {"id": "pay_sg_cli", "order_id": "order_sg_cli", "amount": 100, "currency": "INR", "method": "upi", "status": "captured", "created_at": 1}}}}
raw = json.dumps(payload, separators=(",", ":")).encode()
signature = hmac.new(args.secret.encode(), raw, hashlib.sha256).hexdigest()
request = urllib.request.Request("http://127.0.0.1:8000/api/razorpay/webhook", data=raw, method="POST", headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature, "x-razorpay-event-id": args.event_id})
with urllib.request.urlopen(request) as response:
    print(response.read().decode())
