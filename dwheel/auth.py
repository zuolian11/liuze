import hashlib
import hmac
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, g

# SECRET_KEY and TOKEN_EXPIRE_HOURS are set by server.py
SECRET_KEY = ''
TOKEN_EXPIRE_HOURS = 24

def set_secret_key(key):
    global SECRET_KEY
    SECRET_KEY = key

def hash_password(pw):
    return hashlib.sha256((pw + SECRET_KEY).encode()).hexdigest()

def make_token(user_id, username, role):
    payload = {
        'id': user_id, 'username': username, 'role': role,
        'exp': (datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)).isoformat(),
    }
    data = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    sig = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f'{data}.{sig}'

def verify_token(token):
    try:
        data, sig = token.rsplit('.', 1)
        expected = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(data)
        if payload['exp'] < datetime.utcnow().isoformat():
            return None
        return payload
    except:
        return None

def require_auth(f):
    @wraps(f)
    def decorated(*a, **kw):
        auth_header = request.headers.get('Authorization', '')
        token = auth_header.replace('Bearer ', '') if auth_header.startswith('Bearer ') else ''
        payload = verify_token(token)
        if not payload:
            return jsonify({'ok': False, 'error': 'Unauthorized'}), 401
        g.user = payload
        return f(*a, **kw)
    return decorated
