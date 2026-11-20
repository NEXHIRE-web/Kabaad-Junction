import os
import uuid
import re
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import (Flask, render_template, render_template_string, request,
                   redirect, url_for, abort, session, jsonify)
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
supabase = create_client(SUPABASE_URL, os.getenv("SUPABASE_KEY"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")


@app.context_processor
def inject_globals():
    return {
        "user": session.get("user"),
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_ANON_KEY": SUPABASE_ANON_KEY,
    }


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("home", login=1))
        return f(*args, **kwargs)
    return wrapper


@app.route("/")
def home():
    return render_template("index.html")


CALLBACK_HTML = """<!DOCTYPE html><html><body><p>Login हो रहा है...</p>
<script>
const p = new URLSearchParams(location.hash.slice(1));
const t = p.get('access_token');
if (!t) { location.href = '/?login=1'; }
else {
  fetch('/session-login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({access_token: t})
  }).then(r => { location.href = r.ok ? '/' : '/?login=1'; });
}
</script></body></html>"""


@app.route("/auth/callback")
def auth_callback():
    return render_template_string(CALLBACK_HTML)


@app.route("/session-login", methods=["POST"])
def session_login():
    token = (request.get_json(silent=True) or {}).get("access_token")
    if not token:
        return jsonify(ok=False), 400
    try:
        u = supabase.auth.get_user(token).user
    except Exception:
        return jsonify(ok=False), 401
    if not u:
        return jsonify(ok=False), 401
    meta = u.user_metadata or {}
    session["user"] = {
        "id": u.id,
        "email": u.email,
        "name": meta.get("full_name") or meta.get("name") or u.email,
    }
    return jsonify(ok=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))
@app.route("/newpage", methods=["GET", "POST"])
def newpage():
    if request.method == "POST":
        u = session.get("user")
        if not u:
            return redirect(url_for("home", login=1))
            
        mobile = (request.form.get("mobile") or "").strip()
        if not re.fullmatch(r"[6-9]\d{9}", mobile):
            return "सही 10 अंकों का मोबाइल नंबर डालिए", 400
        mobile = "+91" + mobile
        
        if not is_admin(u):
            since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            cnt = supabase.table("scrap_items").select("id", count="exact") \
                .eq("user_email", u["email"]).gte("created_at", since).execute()
            if (cnt.count or 0) >= 4:
                return "आज की 4 कार्ड की सीमा पूरी हो चुकी है। कल फिर कोशिश कीजिए।", 429

        image_url = None
        photo = request.files.get("photo")
        ...

        image_url = None
        photo = request.files.get("photo")
        if photo and photo.filename:
            ext = photo.filename.rsplit(".", 1)[-1].lower()
            if ext in ("jpg", "jpeg", "png", "webp"):
                fname = f"{uuid.uuid4().hex}.{ext}"
                supabase.storage.from_("scrap_photos").upload(
                    fname, photo.read(), {"content-type": photo.mimetype}
                )
                image_url = supabase.storage.from_("scrap_photos").get_public_url(fname)

        supabase.table("scrap_items").insert({
            "name": request.form.get("name"),
            "mobile": mobile,
            "address": request.form.get("address"),
            "material": request.form.get("material_name"),
            "quantity": request.form.get("quantity") + " " + request.form.get("unit"),
            "image_url": image_url,
            "user_id": u["id"],
            "user_email": u["email"],
        }).execute()
        return redirect(url_for("newpage"))

    res = supabase.table("scrap_items").select("id, material, quantity, image_url,created_at,sold,user_email,address") \
        .order("id", desc=True).execute()
    user = session.get("user")
    return render_template("newpage.html", materials=res.data, user=user, is_admin=is_admin(user))
@app.route("/material/<int:material_id>")
@login_required
def material(material_id):
    res = supabase.table("scrap_items").select("*").eq("id", material_id).execute()
    if not res.data:
        abort(404)
    return render_template("material.html", m=res.data[0])
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
def is_admin(user):
    return bool(user) and user.get("email") == ADMIN_EMAIL


def time_ago(ts):
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - t).total_seconds())
    if secs < 60:
        return "अभी अभी"
    mins = secs // 60
    if mins < 60:
        return f"{mins} मिनट पहले"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs} घंटे पहले"
    days = hrs // 24
    if days < 30:
        return f"{days} दिन पहले"
    months = days // 30
    if months < 12:
        return f"{months} महीने पहले"
    return f"{months // 12} साल पहले"


app.jinja_env.filters["time_ago"] = time_ago
@app.route("/sold/<int:card_id>", methods=["POST"])
def mark_sold(card_id):
    user = session.get("user")
    if not user:
        abort(403)
    res = supabase.table("scrap_items").select("user_email").eq("id", card_id).execute()
    if not res.data:
        abort(404)
    if res.data[0]["user_email"] != user["email"] and not is_admin(user):
        abort(403)
    supabase.table("scrap_items").update({"sold": True}).eq("id", card_id).execute()
    return redirect("/newpage")


@app.route("/delete/<int:card_id>", methods=["POST"])
def delete_card(card_id):
    user = session.get("user")
    if not is_admin(user):
        abort(403)
    supabase.table("scrap_items").delete().eq("id", card_id).execute()
    return redirect("/newpage")

if __name__ == "__main__":
    app.run(debug=False)