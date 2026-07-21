import os
import datetime
from arcgis.gis import GIS
from arcgis.features import FeatureLayer, FeatureLayerCollection
import pandas as pd
from sqlalchemy import create_engine, text

def limpiar_numero(valor):
    """Convierte texto o valores de AGOL ('SI', 'NO', None) a float o 0.0"""
    try:
        if pd.isna(valor):
            return 0.0
        # Intentar convertir directamente si es numero o string numerico
        return float(valor)
    except (ValueError, TypeError):
        # Si viene 'SI', 'NO' u otro texto no convertible, devuelve 0.0
        return 0.0

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

    # 1. Filtrar las 6 estaciones
    codigos_objetivo = ('30226', '30174', '30175', '30206', '30491', '30499')
    codigos_str = ",".join([f"'{c}'" for c in codigos_objetivo])
    where_clause = f"Codigo IN ({codigos_str})"
    
    print(f"Consultando estaciones filtradas: {codigos_objetivo}")
    sdf = feature_layer.query(where=where_clause).sdf
    
    if sdf.empty:
        print("No se encontraron registros para las estaciones indicadas.")
        return

    engine = create_engine(db_url)
    fecha_actual = datetime.datetime.now(datetime.timezone.utc)

    # 2. Datos fijos (Tabla 'estaciones')
    df_estaciones = pd.DataFrame()
    df_estaciones['codigo'] = sdf['Codigo'].astype(str)
    df_estaciones['nombre'] = sdf['Nombre']
    df_estaciones['propietario'] = sdf['Propietario'] if 'Propietario' in sdf.columns else 'Sin propietario'
    df_estaciones['ciudad'] = sdf['Ciudad'] if 'Ciudad' in sdf.columns else 'Sin ciudad'
    df_estaciones['latitud'] = sdf['Latitud']
    df_estaciones['longitud'] = sdf['Longitud']

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

    # 3. Datos dinámicos con conversión y limpieza de campos
    df_lecturas = pd.DataFrame()
    df_lecturas['codigo_estacion'] = sdf['Codigo'].astype(str)
    
    # Identificar columna de caudal
    col_caudal = 'Caudal (l/s)' if 'Caudal (l/s)' in sdf.columns else 'Caudal'
    df_lecturas['caudal_ls'] = sdf[col_caudal].apply(limpiar_numero)
    
    # Identificar columna de nivel
    col_nivel = 'Nivel (m)' if 'Nivel (m)' in sdf.columns else 'Nivel'
    df_lecturas['nivel_m'] = sdf[col_nivel].apply(limpiar_numero)
    
    df_lecturas['fecha_registro'] = fecha_actual

    # Insertar lecturas históricas
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