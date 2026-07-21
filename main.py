import os
import datetime
from arcgis.gis import GIS
from arcgis.features import FeatureLayer, FeatureLayerCollection
import pandas as pd
from sqlalchemy import create_engine, text

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

    print(f"Ítem encontrado: '{item.title}' (Tipo: {item.type})")

    # Obtención de la capa segura sin disparar KeyError de la librería de Esri
    feature_layer = None
    
    # Intento 1: Intentar tratarlo como FeatureLayerCollection
    try:
        flc = FeatureLayerCollection.fromitem(item)
        if flc and len(flc.layers) > 0:
            feature_layer = flc.layers[0]
            print("Capa obtenida vía FeatureLayerCollection.")
    except Exception as e:
        print(f"No es una colección de capas ({e}). Intentando acceso directo por URL...")

    # Intento 2: Si falla el 1, conectar directamente por la URL del servicio
    if feature_layer is None and hasattr(item, 'url') and item.url:
        feature_layer = FeatureLayer(item.url, gis=gis)
        print("Capa obtenida vía URL directa.")

    if feature_layer is None:
        raise ValueError(f"No se pudo extraer una FeatureLayer válida del ítem '{item.title}'.")

    # 1. Filtrar las 6 estaciones objetivo
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

    # 2. Separar datos fijos (Tabla 'estaciones')
    df_estaciones = pd.DataFrame()
    df_estaciones['codigo'] = sdf['Codigo'].astype(str)
    df_estaciones['nombre'] = sdf['Nombre']
    df_estaciones['propietario'] = sdf['Propietario'] if 'Propietario' in sdf.columns else 'Sin propietario'
    df_estaciones['ciudad'] = sdf['Ciudad'] if 'Ciudad' in sdf.columns else 'Sin ciudad'
    df_estaciones['latitud'] = sdf['Latitud']
    df_estaciones['longitud'] = sdf['Longitud']

    # Upsert en la tabla 'estaciones'
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

    # 3. Separar datos dinámicos de lecturas (Tabla 'lecturas_estaciones')
    df_lecturas = pd.DataFrame()
    df_lecturas['codigo_estacion'] = sdf['Codigo'].astype(str)
    df_lecturas['caudal_ls'] = sdf['Caudal (l/s)'] if 'Caudal (l/s)' in sdf.columns else sdf.get('Caudal', 0.0)
    df_lecturas['nivel_m'] = sdf['Nivel (m)'] if 'Nivel (m)' in sdf.columns else sdf.get('Nivel', 0.0)
    df_lecturas['fecha_registro'] = fecha_actual

    # Insertar lecturas históricas
    print(f"Insertando {len(df_lecturas)} lecturas históricas...")
    df_lecturas.to_sql(
        'lecturas_estaciones', 
        engine, 
        if_exists='append', 
        index=False,
        method='multi'
    )
    print("Ingesta finalizada con éxito.")

if __name__ == "__main__":
    ejecutar_ingesta()