from flask import Flask, render_template, request, redirect, session
from datetime import datetime, timedelta
import psycopg2
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "clave_super_segura_cambiar_en_produccion")

DATABASE_URL = os.environ.get("DATABASE_URL")

# =========================
# CONEXIÓN
# =========================
def get_connection():
    return psycopg2.connect(DATABASE_URL)

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
    conn = get_connection()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS prestamos (
            id SERIAL PRIMARY KEY,
            nombre TEXT,
            cedula TEXT,
            celular TEXT,
            monto NUMERIC,
            interes NUMERIC,
            fecha_prestamo DATE,
            fecha_pago DATE,
            medio TEXT,
            objeto TEXT,
            pagado BOOLEAN DEFAULT FALSE,
            tipo_prestamo TEXT DEFAULT 'fijo',
            plazo_dias INTEGER DEFAULT 30
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS abonos (
            id SERIAL PRIMARY KEY,
            prestamo_id INTEGER REFERENCES prestamos(id) ON DELETE CASCADE,
            fecha DATE,
            monto NUMERIC
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS configuracion (
            id SERIAL PRIMARY KEY,
            mora_diaria NUMERIC
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            usuario TEXT UNIQUE,
            password TEXT
        )
    ''')

    c.execute("SELECT COUNT(*) FROM usuarios")
    if c.fetchone()[0] == 0:
        usuario = "admin"
        password = generate_password_hash("Monteria12####")
        c.execute(
            "INSERT INTO usuarios (usuario, password) VALUES (%s, %s)",
            (usuario, password)
        )

    c.execute("SELECT COUNT(*) FROM configuracion")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO configuracion (mora_diaria) VALUES (%s)", (0.5,))

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

        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id, password FROM usuarios WHERE usuario = %s", (usuario,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user[1], password):
            session["usuario_id"] = user[0]
            return redirect("/")
        else:
            return render_template("login.html", error="Credenciales incorrectas")

    return render_template("login.html")

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
    if pagado:
        return 0, 0

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT mora_diaria FROM configuracion LIMIT 1")
    mora_porcentaje = float(c.fetchone()[0])
    conn.close()

    hoy = datetime.now().date()
    if hoy > fecha_vencimiento:
        dias_vencidos = (hoy - fecha_vencimiento).days
        mora = base * (mora_porcentaje / 100) * dias_vencidos
        return round(mora, 2), dias_vencidos

    return 0, 0

# =========================
# INDEX
# =========================
@app.route('/')
@login_required
def index():
    conn = get_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM prestamos")
    prestamos = c.fetchall()
    prestamos_procesados = []

    for p in prestamos:
        prestamo_id = p[0]

        c.execute("SELECT COALESCE(SUM(monto),0) FROM abonos WHERE prestamo_id = %s", (prestamo_id,))
        total_abonos = float(c.fetchone()[0])

        fecha_prestamo = p[6]
        hoy = datetime.now().date()
        dias_transcurridos = (hoy - fecha_prestamo).days
        capital = float(p[4])
        interes_porcentaje = float(p[5])
        pagado = p[10]

        if p[11] == "fijo":
            fecha_vencimiento = fecha_prestamo + timedelta(days=p[12])
            interes = capital * (interes_porcentaje / 100)
            base = capital + interes
            mora, dias = calcular_mora(base, fecha_vencimiento, pagado)
            total_deuda = base + mora
            deuda_restante = max(total_deuda - total_abonos, 0)
            dias_restantes = (fecha_vencimiento - hoy).days
        else:
            ciclos = dias_transcurridos // 30
            interes = capital * (interes_porcentaje / 100) * (ciclos + 1)
            mora = 0
            dias = 0
            dias_restantes = "∞"
            deuda_restante = max((capital + interes) - total_abonos, 0)

        prestamos_procesados.append({
            "id": p[0],
            "nombre": p[1],
            "cedula": p[2],
            "celular": p[3],
            "monto": capital,
            "interes": interes_porcentaje,
            "fecha_prestamo": p[6],
            "fecha_pago": p[7],
            "medio": p[8],
            "objeto": p[9],
            "pagado": pagado,
            "tipo_prestamo": p[11],
            "mora": mora,
            "dias": dias,
            "dias_restantes": dias_restantes,
            "total": round(deuda_restante, 2)
        })

    conn.close()
    return render_template('index.html', prestamos=prestamos_procesados)

# =========================
# 📊 CONTROL REAL DE GANANCIAS (CORREGIDO PROFESIONAL)
# =========================
@app.route('/estadisticas')
@login_required
def estadisticas():
    return "LA RUTA FUNCIONA"

    # Total capital prestado histórico
    c.execute("SELECT COALESCE(SUM(monto),0) FROM prestamos")
    total_prestado = float(c.fetchone()[0])

    # Total dinero ingresado por abonos
    c.execute("SELECT COALESCE(SUM(monto),0) FROM abonos")
    total_abonos = float(c.fetchone()[0])

    # Capital en la calle (préstamos no pagados)
    c.execute("SELECT COALESCE(SUM(monto),0) FROM prestamos WHERE pagado = FALSE")
    capital_en_calle = float(c.fetchone()[0])

    # =========================
    # GANANCIA REAL CORRECTA
    # =========================

    # Traemos préstamos y cuánto han abonado
    c.execute("""
        SELECT p.id, p.monto,
               COALESCE(SUM(a.monto),0) as total_abonado
        FROM prestamos p
        LEFT JOIN abonos a ON p.id = a.prestamo_id
        GROUP BY p.id
    """)
    datos = c.fetchall()

    ganancia_real = 0

    for prestamo_id, capital, abonado in datos:
        capital = float(capital)
        abonado = float(abonado)

        if abonado > capital:
            ganancia_real += (abonado - capital)

    # =========================
    # GANANCIA PROYECTADA
    # =========================

    c.execute("SELECT monto, interes FROM prestamos WHERE pagado = FALSE")
    prestamos_activos = c.fetchall()

    ganancia_proyectada = 0
    for capital, interes in prestamos_activos:
        capital = float(capital)
        interes = float(interes)
        ganancia_proyectada += capital * (interes / 100)

    # Total interés histórico generado
    c.execute("SELECT monto, interes FROM prestamos")
    prestamos = c.fetchall()

    total_interes_generado = 0
    for capital, interes in prestamos:
        capital = float(capital)
        interes = float(interes)
        total_interes_generado += capital * (interes / 100)

    conn.close()

    return render_template("estadisticas.html",
        total_prestado=round(total_prestado,2),
        total_abonos=round(total_abonos,2),
        total_interes=round(total_interes_generado,2),
        capital_en_calle=round(capital_en_calle,2),
        ganancia_real=round(ganancia_real,2),
        ganancia_proyectada=round(ganancia_proyectada,2)
    )
# =========================
# RUN
# =========================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)