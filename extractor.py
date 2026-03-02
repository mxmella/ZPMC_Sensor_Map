import pdfplumber
import fitz # PyMuPDF
import json
import re
import os
import glob
import datetime

# Configuración de directorios
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = BASE_DIR
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
OUTPUT_FILE = os.path.join(BASE_DIR, "sensores_db.json")

# Asegurar que existan los directorios
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

def extraer_sensores():
    lista_sensores = []
    
    # Patrones Regex para ZPMC
    # Tags: SQ (Limit Switch), LS (Limit Switch), B (Sensor), PX (Proximity)
    # Agregamos M (Motores) y Y (Frenos/Solenoides) para el análisis de potencia
    # \b asegura que coincida con la palabra completa (ej. SQ10 y no SQ100 por error parcial)
    # Agregamos K (Relés), S (Botones), H (Luces) para mayor cobertura
    # MEJORA: Regex más flexible para capturar variantes como M.1, M-1, M 1, SQ-10, etc.
    # Agregamos U (Variadores/Convertidores) y VFD para esquema de potencia
    patron_tag = re.compile(r'\b((?:SQ|LS|B|PX|M|Y|K|S|H|U|VFD)\s*[-.]?\s*\d+)\b', re.IGNORECASE)
    
    # Base de Conocimiento ZPMC / IEC (Motor de Inferencia)
    KNOWLEDGE_BASE = {
        "SQ": "Limit Switch (Posición)",
        "LS": "Limit Switch (Seguridad)",
        "B":  "Sensor / Transductor",
        "PX": "Sensor Proximidad",
        "M":  "Motor Eléctrico",
        "Y":  "Freno / Solenoide / Válvula",
        "K":  "Relé / Contactor",
        "S":  "Pulsador / Selector",
        "H":  "Luz Piloto / Indicador",
        "U":  "Variador de Frecuencia / Convertidor",
        "VFD": "Variador de Frecuencia (VFD)"
    }

    # NUEVO: Mapa de Traducción de Funciones ZPMC (Ubicación Real)
    ZPMC_LOCATIONS = {
        "=010": "Main Hoist (Elevación Principal)",
        "=020": "Main Trolley (Carro)",
        "=030": "Boom Hoist (Pluma)",
        "=040": "Gantry (Traslación)",
        "=050": "Spreader",
        "=060": "Lubrication System",
        "=070": "E-House / Power Supply",
        "=080": "Lighting / Auxiliaries",
        "=003": "General Control / CMS"
    }

    # PLC Inputs/Outputs: Soporta %I0.0, I50.0 (sin %), I 50.0 (con espacio) y formato Allen Bradley
    # Regex mejorado para capturar variantes sin % y con espacios
    patron_plc = re.compile(r'(%?[IQ]\s*\d+\.\d+|[IO]:\s*\d+/\d+)', re.IGNORECASE)

    # Detección de Voltaje (ej: 24VDC, 400V, 110VAC, 3.3kV)
    patron_voltaje = re.compile(r'\b(\d+(?:\.\d+)?\s*[kK]?[vV](?:[aA][cC]|[dD][cC])?)\b', re.IGNORECASE)

    # Detección de Potencia (ej: 30KW, 7.5kW, 150HP)
    patron_potencia = re.compile(r'\b(\d+(?:\.\d+)?\s*(?:[kK][wW]|[hH][pP]))\b', re.IGNORECASE)

    # Detección de Función de Planta ZPMC (ej: =040, =010.M, =.G)
    patron_funcion_planta = re.compile(r'(=[\d\.]+[A-Z]?)')

    archivos_pdf = glob.glob(os.path.join(DATA_DIR, "*.pdf"))
    
    if not archivos_pdf:
        print(f"⚠️ No se encontraron archivos PDF en: {DATA_DIR}")
        return

    print(f"🚀 Iniciando escaneo de {len(archivos_pdf)} planos...")

    for ruta_pdf in archivos_pdf:
        nombre_archivo = os.path.basename(ruta_pdf)
        print(f"📄 Procesando: {nombre_archivo}")
        
        try:
            with pdfplumber.open(ruta_pdf) as pdf:
                for i, pagina in enumerate(pdf.pages):
                    texto = pagina.extract_text()
                    if not texto:
                        continue
                    
                    # Buscar metadatos de la página (Page description y Object Loc)
                    descripcion_pagina = ""
                    ubicacion_objeto = ""

                    # Buscar Función de Planta Global en la página (contexto general)
                    funcion_planta_pag = ""
                    match_func_page = patron_funcion_planta.search(texto)
                    if match_func_page:
                        funcion_planta_pag = match_func_page.group(1)

                    # --- LÓGICA DE CAJETÍN (ZPMC CORONEL) ---
                    # Intentamos leer el código del sistema en el pie de página
                    sistema_detectado = "General / Desconocido"
                    try:
                        # Cortamos el área inferior donde suele estar el código =0XX
                        # Usamos coordenadas relativas al tamaño de página para mayor seguridad
                        alto = pagina.height
                        ancho = pagina.width
                        pie_pagina = pagina.crop((0, alto * 0.85, ancho, alto)).extract_text() or ""
                        
                        for codigo, nombre in ZPMC_LOCATIONS.items():
                            if codigo in pie_pagina:
                                sistema_detectado = nombre
                                break
                    except:
                        pass # Si falla el crop, seguimos con el valor por defecto

                    lineas_raw = texto.split('\n')
                    for idx_linea, l in enumerate(lineas_raw):
                        l_clean = l.strip()
                        
                        # Búsqueda mejorada para Page Description (insensible a mayúsculas y multilínea)
                        match_desc = re.search(r'Page description[:\s]*(.*)', l, re.IGNORECASE)
                        if match_desc:
                            contenido = match_desc.group(1).strip()
                            if contenido:
                                descripcion_pagina = contenido
                            elif idx_linea + 1 < len(lineas_raw):
                                # Si la etiqueta está vacía, mirar la línea siguiente (común en cajetines)
                                posible_desc = lineas_raw[idx_linea + 1].strip()
                                if len(posible_desc) > 2 and "Object Loc" not in posible_desc:
                                    descripcion_pagina = posible_desc

                        # Búsqueda mejorada para Object Loc
                        match_loc = re.search(r'Object Loc[:\s]*(.*)', l, re.IGNORECASE)
                        if match_loc:
                            contenido = match_loc.group(1).strip()
                            if contenido:
                                ubicacion_objeto = contenido
                        
                        # Heurística ZPMC: Si la línea empieza con "+" (ej: +10F01 PANEL) es una ubicación
                        if not ubicacion_objeto and l_clean.startswith("+") and len(l_clean) < 40:
                            # Filtramos para evitar falsos positivos cortos, pero aceptamos "+10F01 PANEL"
                            ubicacion_objeto = l_clean

                    # Analizamos línea por línea para mantener el contexto
                    lineas = texto.split('\n')
                    for linea in lineas:
                        linea = linea.strip()
                        if len(linea) < 2: # Ignorar líneas vacías o basura
                            continue

                        tags_encontrados = patron_tag.findall(linea)
                        
                        # Buscar si hay dirección de PLC en la misma línea
                        match_plc = patron_plc.search(linea)
                        if match_plc:
                            # Normalizamos quitando espacios (ej: "I 50.0" -> "I50.0") y mayúsculas
                            direccion_plc = match_plc.group(0).replace(" ", "").upper()
                        else:
                            direccion_plc = "No detectado"
                        
                        # Buscar voltaje en la línea
                        match_voltaje = patron_voltaje.search(linea)
                        voltaje = match_voltaje.group(0).upper() if match_voltaje else ""

                        # Buscar potencia en la línea
                        match_potencia = patron_potencia.search(linea)
                        potencia = match_potencia.group(0).upper() if match_potencia else ""

                        # Buscar Función de Planta en la línea (prioridad sobre la de página)
                        match_func_line = patron_funcion_planta.search(linea)
                        funcion_planta = match_func_line.group(1) if match_func_line else funcion_planta_pag

                        # Prioridad de ubicación: 
                        # 1. Si la línea tiene un código explícito (=040...)
                        # 2. Si no, usamos el sistema detectado en el cajetín de la página
                        ubicacion_real = sistema_detectado

                        if tags_encontrados:
                            for tag_raw in tags_encontrados:
                                # Normalización: Eliminar espacios, puntos y guiones para estandarizar (ej: "M.1" -> "M1")
                                tag_clean = re.sub(r'[\s.-]', '', tag_raw.upper())

                                # Inferencia de Tipo (IA basada en reglas)
                                prefix_match = re.match(r'[A-Z]+', tag_clean)
                                prefix = prefix_match.group(0) if prefix_match else ""
                                tipo_comp = KNOWLEDGE_BASE.get(prefix, "Dispositivo")

                                lista_sensores.append({
                                    "tag": tag_clean,
                                    "tipo": tipo_comp,
                                    "voltaje": voltaje,
                                    "potencia": potencia,
                                    "folio_plano": i + 1, # Páginas base 1
                                    "descripcion_encontrada": linea,
                                    "funcion_planta": funcion_planta,
                                    "ubicacion_real": ubicacion_real,
                                    "direccion_plc": direccion_plc,
                                    "archivo": nombre_archivo,
                                    "descripcion_pagina": descripcion_pagina,
                                    "ubicacion_objeto": ubicacion_objeto
                                })
                        elif direccion_plc != "No detectado":
                            # Caso ZPMC: Salidas/Entradas directas sin Tag físico (ej: Q472.1)
                            # Creamos un registro donde el Tag es "PLC-IO" para que sea buscable
                            lista_sensores.append({
                                "tag": "PLC-IO",
                                "tipo": "Entrada/Salida PLC",
                                "voltaje": voltaje,
                                "potencia": potencia,
                                "folio_plano": i + 1,
                                "descripcion_encontrada": linea,
                                "funcion_planta": funcion_planta,
                                "ubicacion_real": ubicacion_real,
                                "direccion_plc": direccion_plc,
                                "archivo": nombre_archivo,
                                "descripcion_pagina": descripcion_pagina,
                                "ubicacion_objeto": ubicacion_objeto
                            })
                        else:
                            # Guardar también el texto general para búsquedas por descripción
                            lista_sensores.append({
                                "tag": "TEXTO",
                                "tipo": "Texto General",
                                "voltaje": voltaje,
                                "potencia": potencia,
                                "folio_plano": i + 1,
                                "descripcion_encontrada": linea,
                                "funcion_planta": funcion_planta,
                                "ubicacion_real": ubicacion_real,
                                "direccion_plc": direccion_plc,
                                    "archivo": nombre_archivo,
                                    "descripcion_pagina": descripcion_pagina,
                                    "ubicacion_objeto": ubicacion_objeto
                            })
                                
        except Exception as e:
            print(f"❌ Error leyendo {nombre_archivo}: {e}")

        # --- EXTRACCIÓN DE IMÁGENES (PyMuPDF) ---
        try:
            doc_fitz = fitz.open(ruta_pdf)
            for i, page in enumerate(doc_fitz):
                image_list = page.get_images(full=True)
                if image_list:
                    for img_index, img in enumerate(image_list):
                        xref = img[0]
                        base_image = doc_fitz.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        
                        # Guardar imagen: NombrePDF_Pagina_Indice.ext
                        clean_name = os.path.splitext(nombre_archivo)[0]
                        image_filename = f"{clean_name}_Pag{i+1}_{img_index}.{image_ext}"
                        with open(os.path.join(IMAGES_DIR, image_filename), "wb") as f_img:
                            f_img.write(image_bytes)
            doc_fitz.close()
        except Exception as e:
            print(f"   ⚠️ No se pudieron extraer imágenes de {nombre_archivo}: {e}")

    # Limpieza de duplicados
    # Creamos un set de tuplas para identificar únicos
    sensores_unicos = {
        (s['tag'], s['folio_plano'], s['archivo'], s['direccion_plc']): s 
        for s in lista_sensores
    }.values()

    # Guardar JSON
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(sensores_unicos), f, indent=4, ensure_ascii=False)

    # --- INICIO ANALISIS INTELIGENTE ---
    print("\n🧠 Analizando arquitectura de la grúa...")
    
    # Estructura para contar componentes ÚNICOS (usamos sets para evitar duplicados)
    analisis_sistemas = {}

    for s in sensores_unicos:
        if s['tag'] == 'TEXTO': continue
        
        # Usar la ubicación real traducida
        sistema = s.get('ubicacion_real', 'Otros')
        
        # Inicializar sistema si no existe
        if sistema not in analisis_sistemas:
            analisis_sistemas[sistema] = {"Motores": set(), "Sensores": set()}
        
        # Clasificar componente
        tag = s['tag'].upper()
        if tag.startswith("M") and tag[1:].isdigit(): # Es un Motor (ej: M1, M20)
            analisis_sistemas[sistema]["Motores"].add(tag)
        elif tag.startswith(("SQ", "LS", "B", "PX")): # Es un Sensor
            analisis_sistemas[sistema]["Sensores"].add(tag)

    # Imprimir reporte ejecutivo en consola
    print("-" * 60)
    print(f"{'SISTEMA':<15} | {'MOTORES':<10} | {'SENSORES':<10}")
    print("-" * 60)
    for sys, data in analisis_sistemas.items():
        n_motores = len(data["Motores"])
        n_sensores = len(data["Sensores"])
        if n_motores > 0 or n_sensores > 0:
            print(f"{sys:<15} | {n_motores:<10} | {n_sensores:<10}")
    print("-" * 60)
    # --- FIN ANALISIS ---

    # Guardar también como archivo JS para evitar errores de CORS al abrir index.html localmente
    # Ahora incluimos el reporte de sistemas en el JS
    OUTPUT_JS_FILE = os.path.join(BASE_DIR, "sensores_db.js")
    
    # Convertir sets a conteos para JSON
    reporte_export = {}
    for k, v in analisis_sistemas.items():
        reporte_export[k] = {"motores": len(v["Motores"]), "sensores": len(v["Sensores"])}

    with open(OUTPUT_JS_FILE, 'w', encoding='utf-8') as f:
        json_content = json.dumps(list(sensores_unicos), indent=4, ensure_ascii=False)
        json_reporte = json.dumps(reporte_export, indent=4, ensure_ascii=False)
        timestamp = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
        f.write(f"const datosSensores = {json_content};\n")
        f.write(f"const reporteSistemas = {json_reporte};\n")
        f.write(f"const fechaGeneracion = '{timestamp}';")

    print(f"✅ Extracción completa. {len(sensores_unicos)} registros guardados en output/")

if __name__ == "__main__":
    extraer_sensores()