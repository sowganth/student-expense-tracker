from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import os
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'student_expense_tracker_secret_key_2026')

DATABASE_URL = os.environ.get('DATABASE_URL', '')

# ─── Database Helper ───────────────────────────────────────────────
def get_db():
    """Get a PostgreSQL database connection."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def get_cursor(conn):
    """Return a dict-like cursor."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    """Initialize the database with tables (safe to call on every cold start)."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    cursor.close()
    conn.close()

# Initialize database on startup
if DATABASE_URL:
    init_db()

# ─── Login Required Decorator ─────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ─── Routes ────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Landing page."""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """User registration."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not name or not email or not password:
            flash('All fields are required.', 'error')
            return render_template('register.html')

        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('register.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('register.html')

        conn = get_db()
        cursor = get_cursor(conn)
        try:
            cursor.execute(
                'INSERT INTO users (name, email, password) VALUES (%s, %s, %s)',
                (name, email, generate_password_hash(password))
            )
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            flash('Email already registered.', 'error')
        finally:
            cursor.close()
            conn.close()

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        conn = get_db()
        cursor = get_cursor(conn)
        cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            flash(f'Welcome back, {user["name"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html')

@app.route('/logout')
def logout():
    """User logout."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    """Main dashboard with expense summary."""
    conn = get_db()
    cursor = get_cursor(conn)
    user_id = session['user_id']

    # Total spending
    cursor.execute(
        'SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE user_id = %s',
        (user_id,)
    )
    total = cursor.fetchone()['total']

    # This month spending
    first_of_month = datetime.now().strftime('%Y-%m-01')
    cursor.execute(
        'SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE user_id = %s AND date >= %s',
        (user_id, first_of_month)
    )
    monthly = cursor.fetchone()['total']

    # Today spending
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute(
        'SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE user_id = %s AND date = %s',
        (user_id, today)
    )
    daily = cursor.fetchone()['total']

    # Category-wise spending
    cursor.execute(
        '''SELECT category, SUM(amount) as total, COUNT(*) as count
           FROM expenses WHERE user_id = %s
           GROUP BY category ORDER BY total DESC''',
        (user_id,)
    )
    categories = cursor.fetchall()

    # Recent expenses (last 5)
    cursor.execute(
        '''SELECT * FROM expenses WHERE user_id = %s
           ORDER BY date DESC, created_at DESC LIMIT 5''',
        (user_id,)
    )
    recent = cursor.fetchall()

    # Total expense count
    cursor.execute(
        'SELECT COUNT(*) as count FROM expenses WHERE user_id = %s',
        (user_id,)
    )
    expense_count = cursor.fetchone()['count']

    cursor.close()
    conn.close()

    return render_template('dashboard.html',
                           total=total,
                           monthly=monthly,
                           daily=daily,
                           categories=categories,
                           recent=recent,
                           expense_count=expense_count)

@app.route('/add_expense', methods=['GET', 'POST'])
@login_required
def add_expense():
    """Add a new expense."""
    if request.method == 'POST':
        amount = request.form.get('amount', '')
        category = request.form.get('category', '')
        date = request.form.get('date', '')
        description = request.form.get('description', '').strip()

        if not amount or not category or not date:
            flash('Amount, category, and date are required.', 'error')
            return render_template('add_expense.html')

        try:
            amount = float(amount)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash('Please enter a valid positive amount.', 'error')
            return render_template('add_expense.html')

        conn = get_db()
        cursor = get_cursor(conn)
        cursor.execute(
            'INSERT INTO expenses (user_id, amount, category, date, description) VALUES (%s, %s, %s, %s, %s)',
            (session['user_id'], amount, category, date, description)
        )
        conn.commit()
        cursor.close()
        conn.close()

        flash('Expense added successfully!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('add_expense.html', today=datetime.now().strftime('%Y-%m-%d'))

@app.route('/view_expenses')
@login_required
def view_expenses():
    """View all expenses with filters."""
    conn = get_db()
    cursor = get_cursor(conn)
    user_id = session['user_id']

    # Get filter parameters
    filter_category = request.args.get('category', '')
    filter_date_from = request.args.get('date_from', '')
    filter_date_to = request.args.get('date_to', '')
    sort_by = request.args.get('sort', 'date_desc')

    query = 'SELECT * FROM expenses WHERE user_id = %s'
    params = [user_id]

    if filter_category:
        query += ' AND category = %s'
        params.append(filter_category)

    if filter_date_from:
        query += ' AND date >= %s'
        params.append(filter_date_from)

    if filter_date_to:
        query += ' AND date <= %s'
        params.append(filter_date_to)

    # Sorting (safe whitelist — no user input injected directly)
    sort_map = {
        'date_desc': 'date DESC, created_at DESC',
        'date_asc': 'date ASC, created_at ASC',
        'amount_desc': 'amount DESC',
        'amount_asc': 'amount ASC',
        'category': 'category ASC, date DESC',
    }
    query += f' ORDER BY {sort_map.get(sort_by, "date DESC, created_at DESC")}'

    cursor.execute(query, params)
    expenses = cursor.fetchall()

    # Get all categories for filter dropdown
    cursor.execute(
        'SELECT DISTINCT category FROM expenses WHERE user_id = %s ORDER BY category',
        (user_id,)
    )
    all_categories = cursor.fetchall()

    # Calculate filtered total
    filtered_total = sum(e['amount'] for e in expenses)

    cursor.close()
    conn.close()

    return render_template('view_expenses.html',
                           expenses=expenses,
                           all_categories=all_categories,
                           filter_category=filter_category,
                           filter_date_from=filter_date_from,
                           filter_date_to=filter_date_to,
                           sort_by=sort_by,
                           filtered_total=filtered_total)

@app.route('/delete_expense/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    """Delete an expense."""
    conn = get_db()
    cursor = get_cursor(conn)
    cursor.execute(
        'DELETE FROM expenses WHERE id = %s AND user_id = %s',
        (expense_id, session['user_id'])
    )
    conn.commit()
    cursor.close()
    conn.close()
    flash('Expense deleted successfully.', 'info')
    return redirect(url_for('view_expenses'))

@app.route('/edit_expense/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    """Edit an existing expense."""
    conn = get_db()
    cursor = get_cursor(conn)

    if request.method == 'POST':
        amount = request.form.get('amount', '')
        category = request.form.get('category', '')
        date = request.form.get('date', '')
        description = request.form.get('description', '').strip()

        try:
            amount = float(amount)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash('Please enter a valid positive amount.', 'error')
            cursor.close()
            conn.close()
            return redirect(url_for('edit_expense', expense_id=expense_id))

        cursor.execute(
            '''UPDATE expenses SET amount = %s, category = %s, date = %s, description = %s
               WHERE id = %s AND user_id = %s''',
            (amount, category, date, description, expense_id, session['user_id'])
        )
        conn.commit()
        cursor.close()
        conn.close()
        flash('Expense updated successfully!', 'success')
        return redirect(url_for('view_expenses'))

    cursor.execute(
        'SELECT * FROM expenses WHERE id = %s AND user_id = %s',
        (expense_id, session['user_id'])
    )
    expense = cursor.fetchone()
    cursor.close()
    conn.close()

    if not expense:
        flash('Expense not found.', 'error')
        return redirect(url_for('view_expenses'))

    return render_template('edit_expense.html', expense=expense)

@app.route('/api/chart_data')
@login_required
def chart_data():
    """API endpoint for chart data."""
    conn = get_db()
    cursor = get_cursor(conn)
    user_id = session['user_id']

    # Category breakdown
    cursor.execute(
        '''SELECT category, SUM(amount) as total
           FROM expenses WHERE user_id = %s
           GROUP BY category ORDER BY total DESC''',
        (user_id,)
    )
    categories = cursor.fetchall()

    # Last 7 days spending
    daily_data = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        cursor.execute(
            'SELECT COALESCE(SUM(amount), 0) as total FROM expenses WHERE user_id = %s AND date = %s',
            (user_id, day)
        )
        day_total = cursor.fetchone()['total']
        daily_data.append({
            'date': (datetime.now() - timedelta(days=i)).strftime('%b %d'),
            'total': day_total
        })

    # Monthly spending (last 6 months)
    monthly_data = []
    for i in range(5, -1, -1):
        month_date = datetime.now() - timedelta(days=i * 30)
        month_start = month_date.strftime('%Y-%m-01')
        if i > 0:
            next_month = (month_date + timedelta(days=30))
            month_end = next_month.strftime('%Y-%m-01')
        else:
            month_end = '9999-12-31'
        cursor.execute(
            '''SELECT COALESCE(SUM(amount), 0) as total FROM expenses
               WHERE user_id = %s AND date >= %s AND date < %s''',
            (user_id, month_start, month_end)
        )
        month_total = cursor.fetchone()['total']
        monthly_data.append({
            'month': month_date.strftime('%b %Y'),
            'total': month_total
        })

    cursor.close()
    conn.close()

    return jsonify({
        'categories': [{'name': c['category'], 'total': c['total']} for c in categories],
        'daily': daily_data,
        'monthly': monthly_data
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
