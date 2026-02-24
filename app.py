from flask import Flask, render_template, request, redirect, session
from datetime import datetime, timedelta
import psycopg2
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import pandas as pd
import io
from flask import send_file
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

    # Crear admin si no existe
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
        c.execute(
            "INSERT INTO configuracion (mora_diaria) VALUES (%s)",
            (0.5,)
        )

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
# EDITAR PRESTAMO
# =========================
@app.route('/editar_prestamo/<int:id>', methods=['POST'])
@login_required
def editar_prestamo(id):
    conn = get_connection()
    c = conn.cursor()

    # 🔒 Verificar si está pagado
    c.execute("SELECT pagado FROM prestamos WHERE id = %s", (id,))
    resultado = c.fetchone()

    if not resultado:
        conn.close()
        return redirect('/')

    if resultado[0] == True:
        conn.close()
        return redirect('/')  # Bloquea edición si ya está pagado

    # Si NO está pagado, permitir edición
    c.execute("""
        UPDATE prestamos
        SET nombre = %s,
            cedula = %s,
            celular = %s,
            monto = %s,
            interes = %s,
            fecha_prestamo = %s,
            fecha_pago = %s,
            medio = %s,
            objeto = %s,
            tipo_prestamo = %s,
            plazo_dias = %s
        WHERE id = %s
    """, (
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
        int(request.form['plazo_dias'] or 30),
        id
    ))

    conn.commit()
    conn.close()

    return redirect('/')


# =========================
# 💰 MES PAGADO (solo indefinidos)
# =========================
@app.route("/mes_pagado/<int:id>")
@login_required
def mes_pagado(id):
    conn = get_connection()
    c = conn.cursor()

    # Traer datos del préstamo
    c.execute("SELECT monto, interes, tipo_prestamo FROM prestamos WHERE id = %s", (id,))
    prestamo = c.fetchone()

    if not prestamo:
        conn.close()
        return redirect("/")

    capital = float(prestamo[0])
    interes = float(prestamo[1])
    tipo = prestamo[2]

    # Solo aplicar si es indefinido
    if tipo != "indefinido":
        conn.close()
        return redirect("/")

    # Calcular interés mensual
    interes_mes = capital * (interes / 100)

    hoy = datetime.now().date()

    # Registrar el pago del mes como abono
    c.execute(
        "INSERT INTO abonos (prestamo_id, fecha, monto) VALUES (%s, %s, %s)",
        (id, hoy, interes_mes)
    )

    # Reiniciar contador cambiando fecha_prestamo a hoy
    c.execute(
        "UPDATE prestamos SET fecha_prestamo = %s WHERE id = %s",
        (hoy, id)
    )

    conn.commit()
    conn.close()

    # Ir directo al dashboard
    return redirect("/estadisticas")
# =========================
# GANANCIAS
# =========================
@app.route('/estadisticas')
@login_required
def estadisticas():
    conn = get_connection()
    c = conn.cursor()

    # Capital total prestado
    c.execute("SELECT COALESCE(SUM(monto),0) FROM prestamos")
    total_prestado = float(c.fetchone()[0])

    # Total abonos recibidos
    c.execute("SELECT COALESCE(SUM(monto),0) FROM abonos")
    total_abonos = float(c.fetchone()[0])

    # Intereses generados (solo préstamos activos)
    c.execute("SELECT id, monto, interes, fecha_prestamo, tipo_prestamo, plazo_dias FROM prestamos")
    prestamos = c.fetchall()

    total_interes_generado = 0

    for p in prestamos:
        capital = float(p[1])
        interes_porcentaje = float(p[2])
        fecha_prestamo = p[3]
        tipo = p[4]

        hoy = datetime.now().date()
        dias = (hoy - fecha_prestamo).days

        if tipo == "fijo":
            interes = capital * (interes_porcentaje / 100)
        else:
            ciclos = dias // 30
            interes = capital * (interes_porcentaje / 100) * (ciclos + 1)

        total_interes_generado += interes

    # Capital pendiente en la calle
    c.execute("SELECT COALESCE(SUM(monto),0) FROM prestamos WHERE pagado = FALSE")
    capital_en_calle = float(c.fetchone()[0])

    conn.close()

    ganancia_real = total_abonos + total_prestado
    ganancia_proyectada = total_interes_generado

    return render_template("estadisticas.html",
        total_prestado=round(total_prestado,2),
        total_abonos=round(total_abonos,2),
        total_interes=round(total_interes_generado,2),
        capital_en_calle=round(capital_en_calle,2),
        ganancia_real=round(ganancia_real,2),
        ganancia_proyectada=round(ganancia_proyectada,2)
    )

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
            total_deuda = capital + interes
            mora = 0
            dias = 0
            dias_restantes = "∞"
            deuda_restante = max(total_deuda - total_abonos, 0)

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
# AGREGAR
# =========================
@app.route('/agregar', methods=['POST'])
@login_required
def agregar():
    conn = get_connection()
    c = conn.cursor()

    c.execute('''
        INSERT INTO prestamos
        (nombre, cedula, celular, monto, interes, fecha_prestamo, fecha_pago, medio, objeto, tipo_prestamo, plazo_dias)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

# =========================
# ABONAR
# =========================
@app.route('/abonar/<int:id>', methods=['POST'])
@login_required
def abonar(id):
    conn = get_connection()
    c = conn.cursor()

    c.execute(
        "INSERT INTO abonos (prestamo_id, fecha, monto) VALUES (%s, %s, %s)",
        (id, datetime.now().date(), float(request.form['abono']))
    )

    conn.commit()
    conn.close()
    return redirect('/')

# =========================
# PAGAR
# =========================
@app.route('/pagar/<int:id>')
@login_required
def pagar(id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE prestamos SET pagado = TRUE WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return redirect('/')

# =========================
# ELIMINAR
# =========================
@app.route('/eliminar/<int:id>')
@login_required
def eliminar(id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM prestamos WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return redirect('/')

# =========================
# RUN
# =========================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)