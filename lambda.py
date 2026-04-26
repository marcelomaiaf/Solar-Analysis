import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo


CENT = Decimal("0.01")


class SafeStop(Exception):
    pass


class ApiError(Exception):
    pass


def load_env():
    path = Path(os.environ.get("ENV_FILE", Path(__file__).with_name(".env.lambda")))
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise SafeStop(f"Missing environment variable: {name}")
    return value or ""


def today():
    return datetime.now(ZoneInfo(env("BUSINESS_TIMEZONE", "Asia/Singapore"))).date().isoformat()


def money(value):
    if value in (None, ""):
        raise SafeStop("QuickBooks item has no UnitPrice")
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def as_float(value):
    return float(money(value))


def norm(value):
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in str(value or "")).split())


def cut(value, limit):
    value = str(value or "")
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def response(status, body, content_type="application/json"):
    return {
        "statusCode": status,
        "headers": {"Content-Type": content_type},
        "body": body if content_type == "text/plain" else json.dumps(body, ensure_ascii=False),
    }


def body_bytes(event):
    raw = event.get("body") or ""
    raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    return base64.b64decode(raw) if event.get("isBase64Encoded") else raw


def header(event, name):
    name = name.lower()
    for key, value in (event.get("headers") or {}).items():
        if key.lower() == name:
            return value or ""
    return ""


def verify_meta_signature(event, raw):
    secret = env("META_APP_SECRET")
    if not secret:
        return
    actual = header(event, "x-hub-signature-256")
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise SafeStop("Invalid Meta webhook signature")


def http_json(method, url, token=None, payload=None, timeout=30, service="API"):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"{service} {exc.code}: {detail[:500]}") from exc


def http_form(method, url, headers, form, timeout=30, service="API"):
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"{service} {exc.code}: {detail[:500]}") from exc


def webhook_verification(event):
    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "").upper()
    if method != "GET":
        return None
    qs = event.get("queryStringParameters") or {}
    ok = qs.get("hub.mode") == "subscribe" and qs.get("hub.verify_token") == env("WHATSAPP_VERIFY_TOKEN", required=True)
    return response(200, qs.get("hub.challenge", ""), "text/plain") if ok else response(403, "Forbidden", "text/plain")


def whatsapp_messages(payload):
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                if msg.get("type") == "text":
                    out.append(
                        {
                            "id": msg.get("id", ""),
                            "from": msg.get("from", ""),
                            "text": msg.get("text", {}).get("body", ""),
                        }
                    )
    return out


ORDER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["is_order", "client", "order_date", "delivery_date", "delivery_note", "items", "confidence", "reason"],
    "properties": {
        "is_order": {"type": "boolean"},
        "client": {"type": "string"},
        "order_date": {"type": "string"},
        "delivery_date": {"type": "string"},
        "delivery_note": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "quantity"],
                "properties": {"name": {"type": "string"}, "quantity": {"type": "number"}},
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
}


def extract_order(text, sender):
    instructions = (
        "Extract a food-supply order from a WhatsApp message. Return only JSON. "
        "Do not invent clients, products, quantities, dates, or prices. "
        "Prices come from QuickBooks later, so omit prices. "
        "Use YYYY-MM-DD dates. Mark confidence low if anything required is ambiguous."
    )
    payload = {
        "model": env("OPENAI_MODEL", "gpt-5.4-mini"),
        "instructions": instructions,
        "input": f"Business date: {today()}\nSender: {sender}\nMessage:\n{text}",
        "text": {"format": {"type": "json_schema", "name": "order", "strict": True, "schema": ORDER_SCHEMA}},
    }
    data = http_json(
        "POST",
        f"{env('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')}/responses",
        env("OPENAI_API_KEY", required=True),
        payload,
        int(env("OPENAI_TIMEOUT_SECONDS", "30")),
        "OpenAI",
    )
    output = data.get("output_text")
    if not output:
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    output = content.get("text")
                    break
    if not output:
        raise SafeStop("OpenAI returned no structured output")
    order = json.loads(output)
    if not order.get("is_order") or order.get("confidence") != "high":
        raise SafeStop(f"Order needs manual review: {order.get('reason', 'low confidence')}")
    if not order.get("client") or not order.get("delivery_date") or not order.get("items"):
        raise SafeStop("Order missing client, delivery_date, or items")
    return order


def qbo_base():
    host = "quickbooks.api.intuit.com" if env("QUICKBOOKS_ENVIRONMENT", "sandbox") == "production" else "sandbox-quickbooks.api.intuit.com"
    return f"https://{host}/v3/company/{env('QUICKBOOKS_REALM_ID', required=True)}"


def qbo(method, path, payload=None):
    sep = "&" if "?" in path else "?"
    url = f"{qbo_base()}{path}{sep}minorversion={env('QUICKBOOKS_MINOR_VERSION', '75')}"
    try:
        return http_json(method, url, env("QUICKBOOKS_ACCESS_TOKEN", required=True), payload, int(env("QUICKBOOKS_TIMEOUT_SECONDS", "30")), "QuickBooks")
    except ApiError as exc:
        if "QuickBooks 401" not in str(exc) or not refresh_qbo_token():
            raise
        return http_json(method, url, env("QUICKBOOKS_ACCESS_TOKEN", required=True), payload, int(env("QUICKBOOKS_TIMEOUT_SECONDS", "30")), "QuickBooks")


def refresh_qbo_token():
    required = ["QUICKBOOKS_CLIENT_ID", "QUICKBOOKS_CLIENT_SECRET", "QUICKBOOKS_REFRESH_TOKEN"]
    if any(not env(name) for name in required):
        return False
    auth = base64.b64encode(f"{env('QUICKBOOKS_CLIENT_ID')}:{env('QUICKBOOKS_CLIENT_SECRET')}".encode()).decode()
    data = http_form(
        "POST",
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        {"Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        {"grant_type": "refresh_token", "refresh_token": env("QUICKBOOKS_REFRESH_TOKEN")},
        int(env("QUICKBOOKS_TIMEOUT_SECONDS", "30")),
        "QuickBooks OAuth",
    )
    os.environ["QUICKBOOKS_ACCESS_TOKEN"] = data.get("access_token", "")
    if data.get("refresh_token") and data["refresh_token"] != env("QUICKBOOKS_REFRESH_TOKEN"):
        print("QuickBooks returned a rotated refresh token; persist it in your secret store.", file=sys.stderr)
    return bool(os.environ["QUICKBOOKS_ACCESS_TOKEN"])


def qbo_query(entity):
    records = []
    page_size = int(env("QUICKBOOKS_QUERY_PAGE_SIZE", "1000"))
    start = 1

    while True:
        query = f"SELECT * FROM {entity} WHERE Active = true STARTPOSITION {start} MAXRESULTS {page_size}"
        path = "/query?" + urllib.parse.urlencode({"query": query})
        page = qbo("GET", path).get("QueryResponse", {}).get(entity, [])

        if not page:
            break

        records.extend(page)

        if len(page) < page_size:
            break

        start += page_size

    return records


def aliases(name):
    try:
        return {norm(k): v for k, v in json.loads(env(name, "{}")).items()}
    except Exception as exc:
        raise SafeStop(f"{name} must be a JSON object") from exc


def resolve(name, records, alias_map, fields, label):
    target = alias_map.get(norm(name), name)
    if isinstance(target, dict) and target.get("id"):
        for record in records:
            if str(record.get("Id")) == str(target["id"]):
                return record
        raise SafeStop(f"{label} alias id not found for {name}")

    target_name = target.get("name") if isinstance(target, dict) else target
    matches = [r for r in records if any(norm(r.get(f)) == norm(target_name) for f in fields)]
    if len(matches) == 1:
        return matches[0]
    raise SafeStop(f"Could not safely match {name} to one {label}")


def official_order(extracted, source):
    customers = qbo_query("Customer")
    items = qbo_query("Item")
    customer = resolve(extracted["client"], customers, aliases("CUSTOMER_ALIASES_JSON"), ["DisplayName", "FullyQualifiedName", "CompanyName"], "QuickBooks customer")

    lines = []
    total = Decimal("0")
    item_aliases = aliases("ITEM_ALIASES_JSON")
    for raw in extracted["items"]:
        item = resolve(raw["name"], items, item_aliases, ["Name", "FullyQualifiedName"], "QuickBooks item")
        qty = Decimal(str(raw["quantity"]))
        if qty <= 0:
            raise SafeStop(f"Invalid quantity for {raw['name']}")
        unit_price = money(item.get("UnitPrice"))
        line_total = money(unit_price * qty)
        total += line_total
        lines.append(
            {
                "name": item.get("Name"),
                "quantity": float(qty),
                "unit_price": as_float(unit_price),
                "line_total": as_float(line_total),
                "quickbooks_item_id": item.get("Id"),
            }
        )

    return {
        "client": customer.get("DisplayName") or extracted["client"],
        "order_date": extracted.get("order_date") or today(),
        "delivery_date": extracted["delivery_date"],
        "delivery_note": extracted.get("delivery_note", ""),
        "items": lines,
        "total": as_float(total),
        "confidence": extracted["confidence"],
        "quickbooks_customer_id": customer["Id"],
        "quickbooks_customer_email": (customer.get("PrimaryEmailAddr") or {}).get("Address", ""),
        "quickbooks_terms_id": (customer.get("SalesTermRef") or {}).get("value", ""),
        "source_message_id": source["id"],
    }


def create_invoice(order):
    invoice_lines = []
    tax_code = env("QUICKBOOKS_DEFAULT_TAX_CODE_REF")
    for item in order["items"]:
        detail = {
            "ItemRef": {"value": item["quickbooks_item_id"], "name": item["name"]},
            "Qty": item["quantity"],
            "UnitPrice": item["unit_price"],
        }
        if tax_code:
            detail["TaxCodeRef"] = {"value": tax_code}
        invoice_lines.append(
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": item["line_total"],
                "Description": item["name"],
                "SalesItemLineDetail": detail,
            }
        )

    payload = {
        "CustomerRef": {"value": order["quickbooks_customer_id"], "name": order["client"]},
        "TxnDate": order["order_date"],
        "Line": invoice_lines,
        "PrivateNote": cut(f"WhatsApp {order['source_message_id']} | Delivery {order['delivery_date']} {order['delivery_note']}", 4000),
        "CustomerMemo": {"value": cut(f"Delivery: {order['delivery_date']} {order['delivery_note']}", 1000)},
    }
    if env("QUICKBOOKS_CURRENCY_CODE"):
        payload["CurrencyRef"] = {"value": env("QUICKBOOKS_CURRENCY_CODE")}
    if order.get("quickbooks_terms_id"):
        payload["SalesTermRef"] = {"value": order["quickbooks_terms_id"]}
    if order.get("quickbooks_customer_email"):
        payload["BillEmail"] = {"Address": order["quickbooks_customer_email"]}

    invoice = qbo("POST", "/invoice", payload).get("Invoice")
    if not invoice:
        raise SafeStop("QuickBooks did not return created invoice")
    return invoice


def send_whatsapp(payload):
    url = f"https://graph.facebook.com/{env('WHATSAPP_GRAPH_API_VERSION', 'v20.0')}/{env('WHATSAPP_PHONE_NUMBER_ID', required=True)}/messages"
    return http_json("POST", url, env("WHATSAPP_ACCESS_TOKEN", required=True), payload, int(env("WHATSAPP_TIMEOUT_SECONDS", "20")), "WhatsApp")


def send_manual_review(source, reason):
    return send_whatsapp(
        {
            "messaging_product": "whatsapp",
            "to": env("WHATSAPP_APPROVER_PHONE_NUMBER", required=True),
            "type": "text",
            "text": {
                "preview_url": False,
                "body": cut(f"Manual review required.\nReason: {reason}\nFrom: {source.get('from')}\nMessage: {source.get('text')}", 4096),
            },
        }
    )


def send_approval(order, invoice):
    currency = env("QUICKBOOKS_CURRENCY_CODE", "SGD")
    number = invoice.get("DocNumber") or invoice.get("Id")
    lines = "\n".join(
        f"- {i['name']} x{i['quantity']:g} @ {currency} {i['unit_price']:.2f} = {currency} {i['line_total']:.2f}"
        for i in order["items"]
    )
    body = cut(
        f"Draft invoice created: {number}\n"
        f"Client: {order['client']}\n"
        f"Delivery: {order['delivery_date']} {order['delivery_note']}\n"
        f"Items:\n{lines}\n"
        f"Total: {currency} {order['total']:.2f}\n\n"
        "Choose Approve or Reject.",
        1024,
    )
    return send_whatsapp(
        {
            "messaging_product": "whatsapp",
            "to": env("WHATSAPP_APPROVER_PHONE_NUMBER", required=True),
            "type": "interactive",
            "interactive": {
                "type": "button",
                "header": {"type": "text", "text": "Invoice approval"},
                "body": {"text": body},
                "footer": {"text": cut(f"QuickBooks invoice {number}", 60)},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"approve_invoice_{invoice.get('Id')}", "title": "Approve"}},
                        {"type": "reply", "reply": {"id": f"reject_invoice_{invoice.get('Id')}", "title": "Reject"}},
                    ]
                },
            },
        }
    )


def process_message(msg):
    try:
        extracted = extract_order(msg["text"], msg["from"])
        order = official_order(extracted, msg)
        invoice = create_invoice(order)
        send_approval(order, invoice)
        return {"status": "draft_invoice_created", "message_id": msg["id"], "invoice_id": invoice.get("Id"), "order": order}
    except (SafeStop, ApiError, json.JSONDecodeError) as exc:
        send_manual_review(msg, str(exc))
        return {"status": "manual_review_requested", "message_id": msg.get("id"), "reason": str(exc)}


def lambda_handler(event, context):
    load_env()
    verify = webhook_verification(event)
    if verify:
        return verify

    try:
        raw = body_bytes(event)
        verify_meta_signature(event, raw)
        messages = whatsapp_messages(json.loads(raw.decode("utf-8")))
        results = [process_message(m) for m in messages]
        return response(200, {"ok": True, "results": results or [{"status": "no_messages"}]})
    except SafeStop as exc:
        return response(400, {"ok": False, "error": str(exc)})
    except Exception as exc:
        print(json.dumps({"error": repr(exc)}, ensure_ascii=False), file=sys.stderr)
        return response(500, {"ok": False, "error": "Internal Lambda error"})


if __name__ == "__main__":
    load_env()
    print(json.dumps(lambda_handler(json.loads(sys.stdin.read() or "{}"), None), ensure_ascii=False, indent=2))
