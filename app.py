import io
import json
import os
import sqlite3
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

st.set_page_config(
    page_title="Cotizador Notarial de México", page_icon="⚖️", layout="wide"
)

ACTOS_TRASLATIVOS = [
    "Compraventa",
    "Adjudicación por Ejecución de Convenio",
    "Donación",
]
ACTOS_ACCESORIOS = [
    "Contrato de Crédito / Mutuo",
    "Apertura de Crédito",
    "Garantía Hipotecaria",
    "Constitución de Fideicomiso de Garantía",
    "Procedimiento Convencional de Ejecución",
    "Cancelación de Hipoteca",
    "Cancelación de Gravámenes",
    "Testamento",
]

TARIFA_ART_152 = [
    {"lim_inf": 0.0, "lim_sup": 8952.49, "cuota_fija": 0.0, "tasa": 0.0192},
    {"lim_inf": 8952.50, "lim_sup": 75984.55, "cuota_fija": 171.88, "tasa": 0.0640},
    {"lim_inf": 75984.56, "lim_sup": 133536.07, "cuota_fija": 4461.94, "tasa": 0.1088},
    {"lim_inf": 133536.08, "lim_sup": 155229.80, "cuota_fija": 10723.55, "tasa": 0.1600},
    {"lim_inf": 155229.81, "lim_sup": 185852.57, "cuota_fija": 14194.54, "tasa": 0.1792},
    {"lim_inf": 185852.58, "lim_sup": 374837.88, "cuota_fija": 19682.13, "tasa": 0.2136},
    {"lim_inf": 374837.89, "lim_sup": 590795.99, "cuota_fija": 60000.00, "tasa": 0.2352},
    {"lim_inf": 590796.00, "lim_sup": 1127926.84, "cuota_fija": 110792.83, "tasa": 0.3000},
    {"lim_inf": 1127926.85, "lim_sup": 1503902.46, "cuota_fija": 271932.09, "tasa": 0.3200},
    {"lim_inf": 1503902.47, "lim_sup": 4511707.37, "cuota_fija": 392244.28, "tasa": 0.3400},
    {"lim_inf": 4511707.38, "lim_sup": 999999999.0, "cuota_fija": 1414897.95, "tasa": 0.3500},
]


@st.cache_resource
def cargar_base_datos():
  ruta_json = os.path.join(os.path.dirname(__file__), "tarifas_estados.json")
  with open(ruta_json, "r", encoding="utf-8") as f:
    estados_data = json.load(f)

  conn = sqlite3.connect(":memory:", check_same_thread=False)
  cursor = conn.cursor()
  cursor.execute(
      "CREATE TABLE aranceles (estado TEXT, tipo_tarifa TEXT, lim_inf REAL,"
      " lim_sup REAL, cuota_fija REAL, tasa REAL)"
  )
  cursor.execute("CREATE TABLE rpp_legacy (estado TEXT, cuota_fija REAL)")

  tablas_mapeo = {
      "arancel_notarial": "NOTARIAL",
      "isai": "ISAI",
      "rpp_traslativo": "RPP_TRASLATIVO",
      "rpp_garantia": "RPP_GARANTIA",
      "rpp_procedimiento": "RPP_PROCEDIMIENTO",
      "rpp_cancelacion": "RPP_CANCELACION",
  }

  for estado, datos in estados_data.items():
    if "rpp_cuota_fija" in datos:
      cursor.execute(
          "INSERT INTO rpp_legacy VALUES (?, ?)",
          (estado, datos["rpp_cuota_fija"]),
      )
    for clave_json, tipo_db in tablas_mapeo.items():
      if clave_json in datos:
        for item in datos[clave_json]:
          cursor.execute(
              "INSERT INTO aranceles VALUES (?, ?, ?, ?, ?, ?)",
              (
                  estado,
                  tipo_db,
                  item["lim_inf"],
                  item["lim_sup"],
                  item["cuota_fija"],
                  item["tasa"],
              ),
          )
  conn.commit()
  return conn, list(estados_data.keys())


def calcular_tarifa_marginal(conn, estado, tipo_tarifa, monto):
  cursor = conn.cursor()
  cursor.execute(
      "SELECT lim_inf, cuota_fija, tasa FROM aranceles WHERE estado = ? AND"
      " tipo_tarifa = ? AND ? >= lim_inf AND ? <= lim_sup",
      (estado, tipo_tarifa, monto, monto),
  )
  row = cursor.fetchone()
  return row[1] + ((monto - row[0]) * row[2]) if row else 0.0


def obtener_rpp_dinamico(conn, estado, nombre_acto, monto):
  if nombre_acto in ACTOS_TRASLATIVOS:
    tipo_tarifa = "RPP_TRASLATIVO"
  elif "Procedimiento" in nombre_acto:
    tipo_tarifa = "RPP_PROCEDIMIENTO"
  elif "Cancelación" in nombre_acto:
    tipo_tarifa = "RPP_CANCELACION"
  else:
    tipo_tarifa = "RPP_GARANTIA"

  cursor = conn.cursor()
  cursor.execute(
      "SELECT COUNT(*) FROM aranceles WHERE estado = ? AND tipo_tarifa = ?",
      (estado, tipo_tarifa),
  )
  if cursor.fetchone()[0] > 0:
    return calcular_tarifa_marginal(conn, estado, tipo_tarifa, monto)
  else:
    cursor.execute(
        "SELECT cuota_fija FROM rpp_legacy WHERE estado = ?", (estado,)
    )
    row = cursor.fetchone()
    return row[0] if row else 0.0


def calcular_isr_enajenacion(
    monto_venta,
    costo_terreno,
    costo_construccion,
    anos_tenencia,
    factor_inpc,
):
  anos = max(1, min(20, int(anos_tenencia)))
  factor = max(1.0, float(factor_inpc))
  factor_depreciacion = max(0.20, 1.0 - (0.03 * anos))
  const_depreciada = costo_construccion * factor_depreciacion

  cca_total = (costo_terreno * factor) + (const_depreciada * factor)
  ganancia = max(0.0, monto_venta - cca_total)
  if ganancia == 0:
    return 0.0, 0.0, 0.0

  isr_estatal = ganancia * 0.05
  ganancia_anual = ganancia / anos

  impuesto_anual = 0.0
  for tramo in TARIFA_ART_152:
    if tramo["lim_inf"] <= ganancia_anual <= tramo["lim_sup"]:
      impuesto_anual = tramo["cuota_fija"] + (
          (ganancia_anual - tramo["lim_inf"]) * tramo["tasa"]
      )
      break

  tasa_efectiva = impuesto_anual / ganancia_anual if ganancia_anual > 0 else 0.0
  isr_federal_neto = max(0.0, (ganancia * tasa_efectiva) - isr_estatal)
  return isr_estatal, isr_federal_neto, isr_estatal + isr_federal_neto


def generar_pdf_bytes(datos):
  buffer = io.BytesIO()
  doc = SimpleDocTemplate(
      buffer,
      pagesize=letter,
      rightMargin=35,
      leftMargin=35,
      topMargin=35,
      bottomMargin=35,
  )
  styles = getSampleStyleSheet()
  elements = [
      Paragraph(
          "PRESUPUESTO NOTARIAL Y REGISTRAL",
          ParagraphStyle(
              "T",
              fontName="Helvetica-Bold",
              fontSize=14,
              textColor=colors.HexColor("#004080"),
          ),
      )
  ]
  elements.append(
      Paragraph(
          f"<b>Estado de la República:</b> {datos['estado']}", styles["Normal"]
      )
  )

  if datos["comparecientes"]:
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("<b>COMPARECIENTES:</b>", styles["Normal"]))
    for rol, nom in datos["comparecientes"].items():
      elements.append(Paragraph(f"• <b>{rol}:</b> {nom}", styles["Normal"]))

  elements.append(Spacer(1, 8))
  tabla_datos = [["Concepto / Descripción", "Monto (MXN)"]]
  for act in datos["actos"]:
    tag = " (100%)" if act["tipo"] == "Principal" else " (50% Desc.)"
    tabla_datos.append([
        f"Honorarios: {act['nombre']}{tag}\n(Base: ${act['base']:,.2f})",
        f"${act['honorarios']:,.2f}",
    ])
    tabla_datos.append(
        [f"Derechos RPP: {act['nombre']}", f"${act['rpp']:,.2f}"]
    )

  tabla_datos.extend([
      ["SUBTOTAL HONORARIOS ESCRITURA", f"${datos['total_hon']:,.2f}"],
      ["IVA de Honorarios (16%)", f"${datos['iva']:,.2f}"],
      ["Impuesto ISAI (Acumulado)", f"${datos['total_isai']:,.2f}"],
      ["ISR Enajenación (Estatal + Federal)", f"${datos['isr_tot']:,.2f}"],
      [
          "TOTAL DERECHOS REGISTRO PÚBLICO (RPP)",
          f"${datos['total_rpp']:,.2f}",
      ],
      [
          "Gastos Operativos, Gestoría y Firmas",
          f"${datos['gastos_operativos']:,.2f}",
      ],
      ["Ratificación de Pagarés (+ IVA)", f"${datos['ratificaciones']:,.2f}"],
      ["TOTAL NETO DE LA OPERACIÓN", f"${datos['total_escritura']:,.2f}"],
  ])

  t = Table(tabla_datos, colWidths=[330, 160])
  t.setStyle(
      TableStyle([
          ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#004080")),
          ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
          ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
          ("ALIGN", (1, 0), (1, -1), "RIGHT"),
          ("GRID", (0, 0), (-1, -2), 0.5, colors.lightgrey),
          ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e6ecf2")),
          ("VALIGN", (0, 1), (0, -1), "MIDDLE"),
      ])
  )
  elements.append(t)
  doc.build(elements)
  pdf_bytes = buffer.getvalue()
  buffer.close()
  return pdf_bytes


# --- INTERFAZ WEB STREAMLIT ---
st.title("⚖️ Cotizador Notarial Multi-Acto de México")
conn, lista_estados = cargar_base_datos()

col_est, _ = st.columns([1, 2])
with col_est:
  estado_sel = st.selectbox("Estado de la República:", lista_estados)

with st.expander("👤 Comparecientes (Llenar solo aplicables)", expanded=False):
  c1, c2 = st.columns(2)
  vendedor = c1.text_input("Vendedor:")
  comprador = c2.text_input("Comprador:")
  acreditante = c1.text_input("Acreditante:")
  acreditado = c2.text_input("Acreditado:")
  fideicomitente = c1.text_input("Fideicomitente:")
  fiduciario = c2.text_input("Fiduciario:")
  adjudicatario = c1.text_input("Adjudicatario:")

st.subheader("📜 Actos Jurídicos en la Escritura")

# Acto 1 (Campos inicializados en 0.0)
col_a1, col_m1, col_av1 = st.columns([2, 1, 1])
acto1 = col_a1.selectbox(
    "1er Acto (Principal):", ACTOS_TRASLATIVOS + ACTOS_ACCESORIOS
)
monto1 = col_m1.number_input(
    "Monto Operación ($):", min_value=0.0, value=0.0, step=50000.0
)
avaluo1 = col_av1.number_input(
    "Valor Avalúo ($):", min_value=0.0, value=0.0, step=50000.0
)

# Cuadro ISR
c_terr, c_const, anos_ten, f_inpc = 0.0, 0.0, 1, 1.0
if acto1 in ACTOS_TRASLATIVOS:
  with st.expander("📊 Datos para Cálculo de ISR Enajenación", expanded=True):
    ci1, ci2, ci3, ci4 = st.columns(4)
    c_terr = ci1.number_input(
        "Costo Terreno ($):", min_value=0.0, value=0.0
    )
    c_const = ci2.number_input(
        "Costo Construcción ($):", min_value=0.0, value=0.0
    )
    anos_ten = ci3.number_input(
        "Años Tenencia (1-20):", min_value=1, max_value=20, value=1
    )
    f_inpc = ci4.number_input(
        "Factor INPC:", min_value=1.0, value=1.0, step=0.01
    )

# Acto 2
inc_a2 = st.checkbox("Habilitar 2º Acto Simultáneo (50% Desc. Honorarios)")
acto2, monto2, avaluo2 = None, 0.0, 0.0
if inc_a2:
  col_a2, col_m2, col_av2 = st.columns([2, 1, 1])
  acto2 = col_a2.selectbox(
      "2º Acto:",
      ACTOS_ACCESORIOS + ACTOS_TRASLATIVOS,
      index=3,
      key="a2",
  )
  monto2 = col_m2.number_input(
      "Monto Operación ($):",
      min_value=0.0,
      value=0.0,
      step=50000.0,
      key="m2",
  )
  avaluo2 = col_av2.number_input(
      "Valor Avalúo ($):",
      min_value=0.0,
      value=0.0,
      step=50000.0,
      key="av2",
  )

# Acto 3
inc_a3 = st.checkbox("Habilitar 3er Acto Simultáneo (50% Desc. Honorarios)")
acto3, monto3, avaluo3 = None, 0.0, 0.0
if inc_a3:
  col_a3, col_m3, col_av3 = st.columns([2, 1, 1])
  acto3 = col_a3.selectbox(
      "3er Acto:",
      ACTOS_ACCESORIOS + ACTOS_TRASLATIVOS,
      index=4,
      key="a3",
  )
  monto3 = col_m3.number_input(
      "Monto Operación ($):", min_value=0.0, value=0.0, step=50000.0, key="m3"
  )
  avaluo3 = col_av3.number_input(
      "Valor Avalúo ($):", min_value=0.0, value=0.0, step=50000.0, key="av3"
  )

# Acto 4
inc_a4 = st.checkbox("Habilitar 4º Acto Simultáneo (50% Desc. Honorarios)")
acto4, monto4, avaluo4 = None, 0.0, 0.0
if inc_a4:
  col_a4, col_m4, col_av4 = st.columns([2, 1, 1])
  acto4 = col_a4.selectbox(
      "4º Acto:", ACTOS_ACCESORIOS + ACTOS_TRASLATIVOS, key="a4"
  )
  monto4 = col_m4.number_input(
      "Monto Operación ($):", min_value=0.0, value=0.0, step=50000.0, key="m4"
  )
  avaluo4 = col_av4.number_input(
      "Valor Avalúo ($):", min_value=0.0, value=0.0, step=50000.0, key="av4"
  )

# Gastos Extra
st.subheader("💼 Gastos Notariales y Ratificaciones")
cg1, cg2, cg3 = st.columns(3)
gastos_gestoria = cg1.number_input(
    "Gastos y Gestorías ($):", min_value=0.0, value=0.0, step=1000.0
)
firma_foranea = cg2.number_input(
    "Gestoría Firma Foránea ($):", min_value=0.0, value=0.0, step=1000.0
)
num_pagares = cg3.number_input(
    "No. Ratificaciones (Pagarés):", min_value=0, value=0, step=1
)

st.divider()

# Cálculo
if st.button("📊 Calcular Presupuesto", type="primary", use_container_width=True):
  sujetos = {
      "Vendedor": vendedor.strip(),
      "Comprador": comprador.strip(),
      "Acreditante": acreditante.strip(),
      "Acreditado": acreditado.strip(),
      "Fideicomitente": fideicomitente.strip(),
      "Fiduciario": fiduciario.strip(),
      "Adjudicatario": adjudicatario.strip(),
  }
  comp_activos = {k: v for k, v in sujetos.items() if v}

  actos_eval = [
      {
          "activo": True,
          "nombre": acto1,
          "monto": monto1,
          "avaluo": avaluo1,
          "desc": 1.0,
          "tipo": "Principal",
      },
      {
          "activo": inc_a2,
          "nombre": acto2,
          "monto": monto2,
          "avaluo": avaluo2,
          "desc": 0.50,
          "tipo": "Simultáneo",
      },
      {
          "activo": inc_a3,
          "nombre": acto3,
          "monto": monto3,
          "avaluo": avaluo3,
          "desc": 0.50,
          "tipo": "Simultáneo",
      },
      {
          "activo": inc_a4,
          "nombre": acto4,
          "monto": monto4,
          "avaluo": avaluo4,
          "desc": 0.50,
          "tipo": "Simultáneo",
      },
  ]

  total_hon = total_isai = total_rpp = 0.0
  actos_proc = []
  for item in actos_eval:
    if item["activo"] and item["nombre"]:
      base = (
          max(item["monto"], item["avaluo"])
          if item["nombre"] in ACTOS_TRASLATIVOS
          else item["monto"]
      )
      hon = (
          calcular_tarifa_marginal(
              conn, estado_sel, "NOTARIAL", base
          )
          * item["desc"]
      )
      isai = (
          calcular_tarifa_marginal(conn, estado_sel, "ISAI", base)
          if item["nombre"] in ACTOS_TRASLATIVOS
          else 0.0
      )
      rpp = obtener_rpp_dinamico(conn, estado_sel, item["nombre"], base)
      total_hon += hon
      total_isai += isai
      total_rpp += rpp
      actos_proc.append({
          "nombre": item["nombre"],
          "base": base,
          "honorarios": hon,
          "rpp": rpp,
          "tipo": item["tipo"],
      })

  isr_est = isr_fed = isr_tot = 0.0
  if acto1 in ACTOS_TRASLATIVOS:
    isr_est, isr_fed, isr_tot = calcular_isr_enajenacion(
        monto1, c_terr, c_const, anos_ten, f_inpc
    )

  hon_rat = num_pagares * 750.0
  tot_rat = hon_rat + (hon_rat * 0.16)
  tot_gest = gastos_gestoria + firma_foranea
  iva_prin = total_hon * 0.16
  total_esc = (
      total_hon
      + iva_prin
      + total_isai
      + isr_tot
      + total_rpp
      + tot_gest
      + tot_rat
  )

  st.subheader("💵 Desglose Detallado del Presupuesto")
  m1, m2, m3 = st.columns(3)
  m1.metric("Honorarios Notariales", f"${total_hon:,.2f}")
  m2.metric("IVA (16%)", f"${iva_prin:,.2f}")
  m3.metric("Impuesto ISAI", f"${total_isai:,.2f}")

  m4, m5, m6 = st.columns(3)
  m4.metric("Derechos RPP (Total)", f"${total_rpp:,.2f}")
  m5.metric("ISR Total (Estatal + Fed)", f"${isr_tot:,.2f}")
  m6.metric("Gestorías y Ratificaciones", f"${tot_gest + tot_rat:,.2f}")

  st.success(f"### TOTAL ESTIMADO DE LA ESCRITURA: ${total_esc:,.2f} MXN")

  datos_pdf = {
      "estado": estado_sel,
      "comparecientes": comp_activos,
      "actos": actos_proc,
      "total_hon": total_hon,
      "iva": iva_prin,
      "total_isai": total_isai,
      "isr_tot": isr_tot,
      "total_rpp": total_rpp,
      "gastos_operativos": tot_gest,
      "ratificaciones": tot_rat,
      "total_escritura": total_esc,
  }
  pdf_data = generar_pdf_bytes(datos_pdf)

  st.download_button(
      label="📄 Descargar Reporte PDF Oficial",
      data=pdf_data,
      file_name=f"Cotizacion_{estado_sel}.pdf",
      mime="application/pdf",
      use_container_width=True,
  )