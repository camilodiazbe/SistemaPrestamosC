from flask import Flask, render_template, request, redirect, send_file, session
from datetime import datetime, timedelta
import sqlite3
import openpyxl
from io import BytesIO
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import psycopg2



app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "clave_super_segura_cambiar_en_produccion")

# =========================
# PROTEGER RUTAS
# =========================
def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return wrap

# =========================
# INICIALIZAR BASE DE DATOS
# =========================
def init_db():
    DATABASE_URL = os.environ.get("postgresql://cmcash_user:OQFijCuQmKTdK21Y4GkNRojDzcVdt775@dpg-d6cs3jstgctc73ep6ju0-a/cmcash")
    conn = psycopg2.connect(DATABASE_URL)
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS prestamos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT,
            cedula TEXT,
            celular TEXT,
            monto REAL,
            interes REAL,
            fecha_prestamo TEXT,
            fecha_pago TEXT,
            medio TEXT,
            objeto TEXT,
            pagado INTEGER DEFAULT 0,
            tipo_prestamo TEXT DEFAULT 'fijo',
            plazo_dias INTEGER DEFAULT 30
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS abonos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prestamo_id INTEGER,
            fecha TEXT,
            monto REAL
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS configuracion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mora_diaria REAL
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE,
            password TEXT
        )
    ''')

    # Crear admin si no existe
    c.execute("SELECT COUNT(*) FROM usuarios")
    if c.fetchone()[0] == 0:
        usuario = "admin"
        password = generate_password_hash("Monteria12####")
        c.execute("INSERT INTO usuarios (usuario, password) VALUES (?, ?)", (usuario, password))

    c.execute("SELECT COUNT(*) FROM configuracion")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO configuracion (mora_diaria) VALUES (0.5)")

    conn.commit()
    conn.close()

init_db()

# =========================
# LOGIN
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form["usuario"]
        password = request.form["password"]

        conn = sqlite3.connect('prestamos.db')
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["usuario_id"] = user["id"]
            return redirect("/")
        else:
            return render_template("login.html", error="Credenciales incorrectas")

    return render_template("login.html")


# =========================
# CAMBIAR PASSWORD (PROTEGIDO)
# =========================
@app.route("/cambiar_password")
@login_required
def cambiar_password():
    nueva = generate_password_hash("Monteria12####")
    conn = sqlite3.connect('prestamos.db')
    c = conn.cursor()
    c.execute("UPDATE usuarios SET password = ?", (nueva,))
    conn.commit()
    conn.close()
    return "Contraseña actualizada"


# =========================
# LOGOUT
# =========================
@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect("/login")


# =========================
# CALCULAR MORA
# =========================
def calcular_mora(base, fecha_vencimiento, pagado):
    if pagado == 1:
        return 0, 0

    conn = sqlite3.connect('prestamos.db')
    c = conn.cursor()
    c.execute("SELECT mora_diaria FROM configuracion LIMIT 1")
    mora_porcentaje = c.fetchone()[0]
    conn.close()

    hoy = datetime.now().date()
    if hoy > fecha_vencimiento:
        dias_vencidos = (hoy - fecha_vencimiento).days
        mora = base * (mora_porcentaje / 100) * dias_vencidos
        return round(mora, 2), dias_vencidos

    return 0, 0


# =========================
# PÁGINA PRINCIPAL
# =========================
@app.route('/')
@login_required
def index():
    conn = sqlite3.connect('prestamos.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT * FROM prestamos")
    prestamos = c.fetchall()
    prestamos_procesados = []

    for p in prestamos:
        c.execute("SELECT SUM(monto) FROM abonos WHERE prestamo_id = ?", (p["id"],))
        total_abonos = c.fetchone()[0] or 0

        fecha_prestamo = datetime.strptime(p["fecha_prestamo"], "%Y-%m-%d").date()
        hoy = datetime.now().date()
        dias_transcurridos = (hoy - fecha_prestamo).days
        capital = p["monto"]

        if p["tipo_prestamo"] == "fijo":
            fecha_vencimiento = fecha_prestamo + timedelta(days=p["plazo_dias"])
            interes = capital * (p["interes"] / 100)
            base = capital + interes
            mora, dias = calcular_mora(base, fecha_vencimiento, p["pagado"])
            total_deuda = base + mora
            deuda_restante = max(total_deuda - total_abonos, 0)
            dias_restantes = (fecha_vencimiento - hoy).days
        else:
            ciclos = dias_transcurridos // 30
            interes = capital * (p["interes"] / 100) * (ciclos + 1)
            total_deuda = capital + interes
            mora = 0
            dias = 0
            dias_restantes = "∞"
            deuda_restante = max(total_deuda - total_abonos, 0)

        prestamos_procesados.append({
            "id": p["id"],
            "nombre": p["nombre"],
            "cedula": p["cedula"],
            "celular": p["celular"],
            "monto": p["monto"],
            "interes": p["interes"],
            "fecha_prestamo": p["fecha_prestamo"],
            "fecha_pago": p["fecha_pago"],
            "medio": p["medio"],
            "objeto": p["objeto"],
            "pagado": p["pagado"],
            "tipo_prestamo": p["tipo_prestamo"],
            "mora": mora,
            "dias": dias,
            "dias_restantes": dias_restantes,
            "total": round(deuda_restante, 2)
        })

    conn.close()
    return render_template('index.html', prestamos=prestamos_procesados)


# =========================
# RUTAS PROTEGIDAS
# =========================
@app.route('/agregar', methods=['POST'])
@login_required
def agregar():
    conn = sqlite3.connect('prestamos.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO prestamos
        (nombre, cedula, celular, monto, interes, fecha_prestamo, fecha_pago, medio, objeto, tipo_prestamo, plazo_dias)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        request.form['nombre'],
        request.form['cedula'],
        request.form['celular'],
        float(request.form['monto']),
        float(request.form['interes']),
        request.form['fecha_prestamo'],
        request.form['fecha_pago'],
        request.form['medio'],
        request.form['objeto'],
        request.form['tipo_prestamo'],
        int(request.form['plazo_dias'] or 30)
    ))
    conn.commit()
    conn.close()
    return redirect('/')


@app.route('/abonar/<int:id>', methods=['POST'])
@login_required
def abonar(id):
    monto_abono = float(request.form['abono'])
    conn = sqlite3.connect('prestamos.db')
    c = conn.cursor()
    c.execute("INSERT INTO abonos (prestamo_id, fecha, monto) VALUES (?, ?, ?)",
              (id, datetime.now().strftime("%Y-%m-%d"), monto_abono))
    conn.commit()
    conn.close()
    return redirect('/')


@app.route('/pagar/<int:id>')
@login_required
def pagar(id):
    conn = sqlite3.connect('prestamos.db')
    c = conn.cursor()
    c.execute("UPDATE prestamos SET pagado = 1 WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect('/')


@app.route('/eliminar/<int:id>')
@login_required
def eliminar(id):
    conn = sqlite3.connect('prestamos.db')
    c = conn.cursor()
    c.execute("DELETE FROM prestamos WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect('/')


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)