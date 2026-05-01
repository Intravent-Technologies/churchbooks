from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, Response
import logging
import io
import csv
from datetime import datetime
from auth import (
    register_church, verify_user, set_pin, login,
    get_user_by_phone, get_church_by_slug, get_church_stats, add_to_waitlist, get_all_waitlist_entries, get_waitlist_count
)

web = Blueprint('web', __name__, url_prefix='')

ADMIN_PASSWORD = "churchbooks2026"

logging.basicConfig(level=logging.INFO)

# ============================================================
# LOCKED ROUTES — redirect everything to waitlist
# ============================================================

LOCKED_ROUTES = ['/', '/about', '/register', '/login', '/dashboard', '/verify', '/set-pin']

def redirect_to_waitlist():
    return redirect(url_for('web.waitlist_page'), code=302)

@web.route('/')
def landing():
    return redirect_to_waitlist()

@web.route('/about')
def about():
    return redirect_to_waitlist()

@web.route('/register', methods=['GET', 'POST'])
def register_page():
    return redirect_to_waitlist()

@web.route('/login', methods=['GET', 'POST'])
def login_page():
    return redirect_to_waitlist()

@web.route('/verify', methods=['GET', 'POST'])
def verify_page():
    return redirect_to_waitlist()

@web.route('/set-pin', methods=['GET', 'POST'])
def set_pin_page():
    return redirect_to_waitlist()

@web.route('/dashboard')
def dashboard():
    return redirect_to_waitlist()

@web.route('/logout')
def logout():
    return redirect_to_waitlist()

@web.route('/api/check-slug')
def check_slug():
    return redirect_to_waitlist()

# ============================================================
# WAITLIST — Only public route
# ============================================================

@web.route('/waitlist', methods=['GET'])
def waitlist_page():
    count = get_waitlist_count()
    return render_template('waitlist.html', waitlist_count=count)

@web.route('/waitlist', methods=['POST'])
def waitlist_submit():
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    role = request.form.get('role', '').strip()
    current_tracking = request.form.get('current_tracking', '').strip()
    will_pay = request.form.get('will_pay', '').strip()
    price_range = request.form.get('price_range', '').strip()
    features = request.form.getlist('features')
    other_feature = request.form.get('other_feature', '').strip()

    if not all([name, phone, role, current_tracking, will_pay]):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('web.waitlist_page'))

    result = add_to_waitlist(
        name=name,
        phone=phone,
        role=role,
        current_tracking=current_tracking,
        will_pay=will_pay,
        price_range=price_range,
        features=','.join(features) if features else '',
        other_feature=other_feature
    )

    if result['success']:
        flash(f"You're on the list! We'll reach out when it's your turn. 🙏", 'success')
        return redirect(url_for('web.waitlist_page'))
    else:
        if 'already' in result.get('error', '').lower():
            flash(result['error'], 'error')
        else:
            flash(f'Something went wrong: {result["error"]}', 'error')
        return redirect(url_for('web.waitlist_page'))

# ============================================================
# ADMIN BACKEND
# ============================================================

def check_admin():
    """Check if admin is authenticated via session or password."""
    return session.get('admin_authenticated', False)

@web.route('/admin', methods=['GET'])
def admin_login():
    if check_admin():
        return redirect(url_for('web.admin_dashboard'))
    return render_template('admin/login.html')

@web.route('/admin', methods=['POST'])
def admin_login_submit():
    password = request.form.get('password', '').strip()
    if password == ADMIN_PASSWORD:
        session['admin_authenticated'] = True
        return redirect(url_for('web.admin_dashboard'))
    flash('Incorrect password.', 'error')
    return redirect(url_for('web.admin_login'))

@web.route('/admin/logout')
def admin_logout():
    session.pop('admin_authenticated', None)
    return redirect(url_for('web.admin_login'))

@web.route('/admin/dashboard')
def admin_dashboard():
    if not check_admin():
        return redirect(url_for('web.admin_login'))

    entries = get_all_waitlist_entries()

    # Stats
    total = len(entries)
    roles = {}
    current_methods = {}
    will_pay_breakdown = {}

    for e in entries:
        role = e.get('role', 'Unknown')
        roles[role] = roles.get(role, 0) + 1

        method = e.get('current_tracking', 'Unknown')
        current_methods[method] = current_methods.get(method, 0) + 1

        pay = e.get('will_pay', 'Unknown')
        will_pay_breakdown[pay] = will_pay_breakdown.get(pay, 0) + 1

    return render_template('admin/dashboard.html',
                         entries=entries,
                         total=total,
                         roles=roles,
                         current_methods=current_methods,
                         will_pay_breakdown=will_pay_breakdown)

@web.route('/admin/export')
def admin_export():
    if not check_admin():
        return redirect(url_for('web.admin_login'))

    entries = get_all_waitlist_entries()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['ID', 'Name', 'Phone', 'Role', 'Current Tracking',
                     'Will Pay', 'Price Range', 'Features', 'Other Feature',
                     'Status', 'Created At'])

    for e in entries:
        writer.writerow([
            e.get('id', ''),
            e.get('name', ''),
            e.get('phone', ''),
            e.get('role', ''),
            e.get('current_tracking', ''),
            e.get('will_pay', ''),
            e.get('price_range', ''),
            e.get('features', ''),
            e.get('other_feature', ''),
            e.get('status', ''),
            e.get('created_at', '')
        ])

    output.seek(0)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=churchbooks_waitlist_{timestamp}.csv'}
    )

# ============================================================
# REAL-TIME SYNC API
# ============================================================

@web.route('/api/waitlist/count')
def api_waitlist_count():
    """Public endpoint: returns current waitlist count."""
    count = get_waitlist_count()
    return jsonify({"count": count})

@web.route('/api/admin/refresh')
def api_admin_refresh():
    """Admin-only endpoint: returns new entries since last check."""
    if not check_admin():
        return jsonify({"error": "Unauthorized"}), 401

    last_count = request.args.get('last_count', 0, type=int)
    entries = get_all_waitlist_entries()
    total = len(entries)
    new_entries = []

    if total > last_count:
        new_count = total - last_count
        new_entries = entries[:new_count]

    return jsonify({
        "total": total,
        "new_count": total - last_count,
        "new_entries": [{
            "name": e.get("name"),
            "phone": e.get("phone"),
            "role": e.get("role"),
            "created_at": e.get("created_at")
        } for e in new_entries]
    })
