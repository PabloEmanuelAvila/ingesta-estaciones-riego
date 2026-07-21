import os
import datetime
from zoneinfo import ZoneInfo
from arcgis.gis import GIS
from arcgis.features import FeatureLayer, FeatureLayerCollection
import pandas as pd
from sqlalchemy import create_engine, text

def limpiar_numero(valor):
    """Convierte texto o números de AGOL ('643,71', 'SI', 'NO', None) a float correcto"""
    if pd.isna(valor) or valor is None:
        return 0.0
    try:
        val_str = str(valor).strip().replace(',', '.')
        return float(val_str)
    except (ValueError, TypeError):
        return 0.0

def buscar_columna(df, patrones):
    """
    Busca dentro del DataFrame una columna que contenga alguno de los patrones indicados,
    ignorando mayúsculas, espacios y caracteres especiales.
    """
    for col in df.columns:
        col_limpia = col.lower().replace(" ", "").replace("(", "").replace(")", "").replace("/", "")
        for patron in patrones:
            patron_limpio = patron.lower().replace(" ", "").replace("(", "").replace(")", "").replace("/", "")
            if patron_limpio in col_limpia:
                return col
    return None

def ejecutar_ingesta():
    agol_user = os.environ.get("AGOL_USER")
    agol_pass = os.environ.get("AGOL_PASS")
    item_id = os.environ.get("AGOL_ITEM_ID")
    db_url = os.environ.get("DATABASE_URL")

    if not all([agol_user, agol_pass, item_id, db_url]):
        raise ValueError("Faltan variables de entorno obligatorias.")

    print(f"[{datetime.datetime.now()}] Conectando a ArcGIS Online...")
    gis = GIS("https://www.arcgis.com", agol_user, agol_pass)
    
    item = gis.content.get(item_id)
    if item is None:
        raise ValueError(f"No se encontró el ítem con ID {item_id}.")

    feature_layer = None
    try:
        flc = FeatureLayerCollection.fromitem(item)
        if flc and len(flc.layers) > 0:
            feature_layer = flc.layers[0]
    except Exception:
        pass

    if feature_layer is None and hasattr(item, 'url') and item.url:
        feature_layer = FeatureLayer(item.url, gis=gis)

    if feature_layer is None:
        raise ValueError(f"No se pudo extraer una FeatureLayer válida del ítem '{item.title}'.")

    # 1. Filtrar las 6 estaciones de interés
    codigos_objetivo = ('30226', '30174', '30175', '30206', '30491', '30499')
    codigos_str = ",".join([f"'{c}'" for c in codigos_objetivo])
    where_clause = f"Codigo IN ({codigos_str})"
    
    print(f"Consultando estaciones filtradas: {codigos_objetivo}")
    sdf = feature_layer.query(where=where_clause).sdf
    
    if sdf.empty:
        print("No se encontraron registros para las estaciones indicadas.")
        return

    # Imprimir columnas para diagnóstico en los logs de GitHub Actions
    print("--- COLUMNAS ENCONTRADAS EN LA CAPA AGOL ---")
    print(list(sdf.columns))
    print("--------------------------------------------")

    engine = create_engine(db_url)
    fecha_actual = datetime.datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

    # 2. Datos fijos (Tabla 'estaciones')
    col_codigo = buscar_columna(sdf, ['codigo']) or 'Codigo'
    col_nombre = buscar_columna(sdf, ['nombre']) or 'Nombre'
    col_propietario = buscar_columna(sdf, ['propietario'])
    col_ciudad = buscar_columna(sdf, ['ciudad'])
    col_lat = buscar_columna(sdf, ['latitud', 'lat']) or 'Latitud'
    col_lon = buscar_columna(sdf, ['longitud', 'lon']) or 'Longitud'

    df_estaciones = pd.DataFrame()
    df_estaciones['codigo'] = sdf[col_codigo].astype(str)
    df_estaciones['nombre'] = sdf[col_nombre]
    df_estaciones['propietario'] = sdf[col_propietario] if col_propietario else 'Sin propietario'
    df_estaciones['ciudad'] = sdf[col_ciudad] if col_ciudad else 'Sin ciudad'
    df_estaciones['latitud'] = sdf[col_lat]
    df_estaciones['longitud'] = sdf[col_lon]

    with engine.begin() as conn:
        for _, row in df_estaciones.iterrows():
            sql = text("""
                INSERT INTO estaciones (codigo, nombre, propietario, ciudad, latitud, longitud)
                VALUES (:codigo, :nombre, :propietario, :ciudad, :latitud, :longitud)
                ON CONFLICT (codigo) DO UPDATE SET
                    nombre = EXCLUDED.nombre,
                    propietario = EXCLUDED.propietario,
                    ciudad = EXCLUDED.ciudad,
                    latitud = EXCLUDED.latitud,
                    longitud = EXCLUDED.longitud;
            """)
            conn.execute(sql, row.to_dict())

    # 3. Datos dinámicos (Tabla 'lecturas_estaciones')
    col_caudal = buscar_columna(sdf, ['caudal', 'caudalls'])
    col_nivel = buscar_columna(sdf, ['nivel', 'nivelm'])

    print(f"Columna de caudal detectada: '{col_caudal}'")
    print(f"Columna de nivel detectada: '{col_nivel}'")
    if col_caudal:
        print("Valores crudos de caudal (AGOL) por estacion:")
        print(sdf[[col_codigo, col_caudal]].to_string(index=False))
    if col_nivel:
        print("Valores crudos de nivel (AGOL) por estacion:")
        print(sdf[[col_codigo, col_nivel]].to_string(index=False))

    df_lecturas = pd.DataFrame()
    df_lecturas['codigo_estacion'] = sdf[col_codigo].astype(str)
    
    if col_caudal:
        df_lecturas['caudal_ls'] = sdf[col_caudal].apply(limpiar_numero)
    else:
        print("ADVERTENCIA: No se encontró la columna de caudal.")
        df_lecturas['caudal_ls'] = 0.0

    if col_nivel:
        df_lecturas['nivel_m'] = sdf[col_nivel].apply(limpiar_numero)
    else:
        print("ADVERTENCIA: No se encontró la columna de nivel.")
        df_lecturas['nivel_m'] = 0.0

    df_lecturas['fecha_registro'] = fecha_actual

    print(f"Insertando {len(df_lecturas)} lecturas históricas en Supabase...")
    df_lecturas.to_sql(
        'lecturas_estaciones', 
        engine, 
        if_exists='append', 
        index=False,
        method='multi'
    )
    print("¡Ingesta finalizada con éxito!")

if __name__ == "__main__":
    ejecutar_ingesta()