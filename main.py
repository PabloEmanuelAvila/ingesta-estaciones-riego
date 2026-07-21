import os
import datetime
from arcgis.gis import GIS
import pandas as pd
from sqlalchemy import create_engine

def ejecutar_ingesta():
    # 1. Leer variables de entorno (seguridad)
    agol_user = os.environ.get("AGOL_USER")
    agol_pass = os.environ.get("AGOL_PASS")
    item_id = os.environ.get("AGOL_ITEM_ID")
    db_url = os.environ.get("DATABASE_URL")

    if not all([agol_user, agol_pass, item_id, db_url]):
        raise ValueError("Faltan variables de entorno obligatorias.")

    print(f"[{datetime.datetime.now()}] Conectando a ArcGIS Online...")
    gis = GIS("https://www.arcgis.com", agol_user, agol_pass)
    
    # 2. Consultar la capa compartida
    item = gis.content.get(item_id)
    feature_layer = item.layers[0]
    
    # Traer todos los registros vigentes
    sdf = feature_layer.query(where="1=1").sdf
    
    if sdf.empty:
        print("No se encontraron datos en la capa.")
        return

    # 3. Mapear y limpiar columnas según la estructura de Supabase
    # Reemplazá los nombres entre comillas dobles si difieren en tu capa original
    df = pd.DataFrame()
    df['codigo'] = sdf['Codigo']
    df['nombre'] = sdf['Nombre']
    df['propietario'] = sdf['propietario'] if 'propietario' in sdf.columns else sdf.get('Propietario')
    df['ciudad'] = sdf['Ciudad']
    df['latitud'] = sdf['Latitud']
    df['longitud'] = sdf['Longitud']
    df['caudal_ls'] = sdf['Caudal']  # o Caudal (l/s) según la capa
    df['nivel_m'] = sdf['Nivel']     # o Nivel (m)
    
    # Marca de tiempo local/UTC de la ingesta
    df['fecha_registro'] = datetime.datetime.now(datetime.timezone.utc)

    # 4. Insertar en PostgreSQL (Supabase)
    print(f"Insertando {len(df)} registros en Supabase...")
    engine = create_engine(db_url)
    
    df.to_sql(
        'lecturas_estaciones', 
        engine, 
        if_exists='append', 
        index=False,
        method='multi'
    )
    print("Ingesta completada exitosamente.")

if __name__ == "__main__":
    ejecutar_ingesta()