"""
Seed data para demo Hagemann.
Ejecutar: docker exec neofreight-hagemann python seed.py
O localmente: DATABASE_URL=postgresql://postgres:localdev@localhost:5432/neofreight python seed.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, date, timedelta
from src.database import engine, Base, init_schema, SessionLocal
from src.models import (
    Empleado, Grupo, CentroCoste, Fichaje, SegmentoTiempo, FuenteFichaje,
    Festivo, PeriodoVacaciones, SolicitudVacaciones, LimiteVacaciones,
    TipoFestivo, TipoAusencia, EstadoSolicitud,
    SaldoHorasMensual,
)

def seed():
    init_schema()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # --- Limpiar datos previos ---
    db.query(SaldoHorasMensual).delete()
    db.query(SolicitudVacaciones).delete()
    db.query(PeriodoVacaciones).delete()
    db.query(LimiteVacaciones).delete()
    db.query(Festivo).delete()
    db.query(SegmentoTiempo).delete()
    db.query(Fichaje).delete()
    db.query(Empleado).delete()
    db.query(CentroCoste).delete()
    db.query(Grupo).delete()
    db.commit()

    # --- Grupos ---
    g_logistik = Grupo(nombre="Logistik", descripcion="Departamento de logística")
    g_verwaltung = Grupo(nombre="Verwaltung", descripcion="Administración")
    g_lager = Grupo(nombre="Lager", descripcion="Almacén")
    db.add_all([g_logistik, g_verwaltung, g_lager])
    db.flush()

    # --- Centros de Coste ---
    cc_logistik = CentroCoste(codigo="4100", nombre="Logistik", color="#3B82F6",
                              descripcion="Departamento de logística y transporte")
    cc_verwaltung = CentroCoste(codigo="4200", nombre="Verwaltung", color="#10B981",
                                descripcion="Administración y oficina")
    cc_lager = CentroCoste(codigo="4300", nombre="Lager", color="#F59E0B",
                           descripcion="Almacén y picking")
    cc_werkstatt = CentroCoste(codigo="4400", nombre="Werkstatt", color="#EF4444",
                               descripcion="Taller mecánico")
    db.add_all([cc_logistik, cc_verwaltung, cc_lager, cc_werkstatt])
    db.flush()

    # --- Empleados ---
    empleados = [
        Empleado(id_nummer=101, nombre="Rene", apellido="Raack",
                 nfc_tag="81 61 07 d2 ad 8b 04", grupo_id=g_logistik.id,
                 monthly_hours=160, email="raack@hagemann.de", activo=True,
                 fecha_alta=date(2020, 3, 1)),
        Empleado(id_nummer=102, nombre="Peter", apellido="Müller",
                 nfc_tag="80 6c 48 d2 73 9d 04", grupo_id=g_logistik.id,
                 monthly_hours=160, activo=True, fecha_alta=date(2019, 6, 15)),
        Empleado(id_nummer=103, nombre="Anna", apellido="Schmidt",
                 nfc_tag="80 6a 43 a2 5f 64 04", grupo_id=g_verwaltung.id,
                 monthly_hours=160, email="schmidt@hagemann.de", activo=True,
                 fecha_alta=date(2021, 1, 10)),
        Empleado(id_nummer=104, nombre="Thomas", apellido="Weber",
                 nfc_tag="80 6c 4f c2 cd f5 04", grupo_id=g_lager.id,
                 monthly_hours=160, activo=True, fecha_alta=date(2022, 8, 1)),
        Empleado(id_nummer=105, nombre="Maria", apellido="Fischer",
                 nfc_tag="80 7a 55 b3 91 a2 04", grupo_id=g_verwaltung.id,
                 monthly_hours=120, activo=True, fecha_alta=date(2023, 2, 20)),
        Empleado(id_nummer=106, nombre="Klaus", apellido="Hoffmann",
                 nfc_tag="80 8b 66 c4 a2 b3 04", grupo_id=g_logistik.id,
                 monthly_hours=160, activo=True, fecha_alta=date(2018, 11, 1)),
        Empleado(id_nummer=107, nombre="Sabine", apellido="Becker",
                 nfc_tag="80 9c 77 d5 b3 c4 04", grupo_id=g_lager.id,
                 monthly_hours=160, activo=True, fecha_alta=date(2021, 5, 15)),
        Empleado(id_nummer=108, nombre="Jürgen", apellido="Zimmermann",
                 nfc_tag="80 ad 88 e6 c4 d5 04", grupo_id=g_logistik.id,
                 monthly_hours=160, activo=True, fecha_alta=date(2017, 3, 1)),
        Empleado(id_nummer=109, nombre="Petra", apellido="Schulz",
                 nfc_tag="80 be 99 f7 d5 e6 04", grupo_id=g_verwaltung.id,
                 monthly_hours=80, activo=True, fecha_alta=date(2024, 1, 15)),
        Empleado(id_nummer=110, nombre="Frank", apellido="Neumann",
                 nfc_tag="80 cf aa 08 e6 f7 04", grupo_id=g_lager.id,
                 monthly_hours=160, activo=True, fecha_alta=date(2020, 9, 1)),
    ]
    db.add_all(empleados)
    db.flush()

    # --- Fichajes de ejemplo (última semana) ---
    today = date.today()
    # Generar 5 días de datos (lun-vie)
    start_of_week = today - timedelta(days=today.weekday())  # lunes

    for day_offset in range(5):  # lun a vie
        day = start_of_week + timedelta(days=day_offset)
        if day > today:
            break

        for emp in empleados:
            # Hora entrada base: 06:00-08:30 según grupo
            if emp.grupo_id == g_logistik.id:
                h_in = 6
            elif emp.grupo_id == g_lager.id:
                h_in = 7
            else:
                h_in = 8

            entrada = datetime(day.year, day.month, day.day, h_in, 0)
            salida = datetime(day.year, day.month, day.day, h_in + 8, 30)
            pausa_inicio = datetime(day.year, day.month, day.day, 12, 0)
            pausa_fin = datetime(day.year, day.month, day.day, 12, 30)

            fichaje = Fichaje(
                empleado_id=emp.id,
                fecha_entrada=entrada,
                fecha_salida=salida,
                minutos_descanso=30,
                fuente="TABLET",
                dispositivo_id="DEMO-TABLET-01",
            )
            db.add(fichaje)
            db.flush()

            # Segmentos: algunos empleados trabajan en 2 departamentos
            if emp.id_nummer in (101, 106):
                # Rene y Klaus: mañana Logistik, tarde Lager
                s1 = SegmentoTiempo(
                    fichaje_id=fichaje.id, empleado_id=emp.id,
                    centro_coste_id=cc_logistik.id,
                    inicio=entrada, fin=pausa_inicio,
                    minutos=int((pausa_inicio - entrada).total_seconds() / 60),
                )
                s2 = SegmentoTiempo(
                    fichaje_id=fichaje.id, empleado_id=emp.id,
                    centro_coste_id=cc_lager.id,
                    inicio=pausa_fin, fin=salida,
                    minutos=int((salida - pausa_fin).total_seconds() / 60),
                )
                db.add_all([s1, s2])
                fichaje.minutos_trabajados = s1.minutos + s2.minutos
            elif emp.id_nummer == 103:
                # Anna: mañana Verwaltung, tarde Logistik (tareas admin de logística)
                s1 = SegmentoTiempo(
                    fichaje_id=fichaje.id, empleado_id=emp.id,
                    centro_coste_id=cc_verwaltung.id,
                    inicio=entrada, fin=pausa_inicio,
                    minutos=int((pausa_inicio - entrada).total_seconds() / 60),
                )
                s2 = SegmentoTiempo(
                    fichaje_id=fichaje.id, empleado_id=emp.id,
                    centro_coste_id=cc_logistik.id,
                    inicio=pausa_fin, fin=salida,
                    minutos=int((salida - pausa_fin).total_seconds() / 60),
                )
                db.add_all([s1, s2])
                fichaje.minutos_trabajados = s1.minutos + s2.minutos
            else:
                # Resto: un solo departamento todo el día
                cc_map = {
                    g_logistik.id: cc_logistik.id,
                    g_verwaltung.id: cc_verwaltung.id,
                    g_lager.id: cc_lager.id,
                }
                cc_id = cc_map.get(emp.grupo_id, cc_logistik.id)
                total_min = int((salida - entrada).total_seconds() / 60) - 30
                s = SegmentoTiempo(
                    fichaje_id=fichaje.id, empleado_id=emp.id,
                    centro_coste_id=cc_id,
                    inicio=entrada, fin=salida,
                    minutos=total_min,
                )
                db.add(s)
                fichaje.minutos_trabajados = total_min

    db.commit()

    # =========================================================
    # --- Festivos Sachsen 2026 ---
    # =========================================================
    festivos_sachsen = [
        # Nacionales (DE)
        Festivo(fecha=date(2026, 1, 1),  nombre="Neujahr",                     bundesland="DE", tipo=TipoFestivo.NACIONAL),
        Festivo(fecha=date(2026, 5, 1),  nombre="Tag der Arbeit",               bundesland="DE", tipo=TipoFestivo.NACIONAL),
        Festivo(fecha=date(2026, 10, 3), nombre="Tag der Deutschen Einheit",     bundesland="DE", tipo=TipoFestivo.NACIONAL),
        Festivo(fecha=date(2026, 12, 25),nombre="1. Weihnachtstag",              bundesland="DE", tipo=TipoFestivo.NACIONAL),
        Festivo(fecha=date(2026, 12, 26),nombre="2. Weihnachtstag",              bundesland="DE", tipo=TipoFestivo.NACIONAL),
        # Regionales Sachsen (SN) — fechas 2026
        # Karfreitag: 3 Abril 2026
        Festivo(fecha=date(2026, 4, 3),  nombre="Karfreitag",                   bundesland="SN", tipo=TipoFestivo.REGIONAL),
        # Ostermontag: 6 Abril 2026
        Festivo(fecha=date(2026, 4, 6),  nombre="Ostermontag",                  bundesland="SN", tipo=TipoFestivo.REGIONAL),
        # Christi Himmelfahrt: 14 Mayo 2026
        Festivo(fecha=date(2026, 5, 14), nombre="Christi Himmelfahrt",          bundesland="SN", tipo=TipoFestivo.REGIONAL),
        # Pfingstmontag: 25 Mayo 2026
        Festivo(fecha=date(2026, 5, 25), nombre="Pfingstmontag",                bundesland="SN", tipo=TipoFestivo.REGIONAL),
        # Reformationstag (Sachsen)
        Festivo(fecha=date(2026, 10, 31),nombre="Reformationstag",              bundesland="SN", tipo=TipoFestivo.REGIONAL),
        # Buß- und Bettag: 18 Noviembre 2026
        Festivo(fecha=date(2026, 11, 18),nombre="Buß- und Bettag",              bundesland="SN", tipo=TipoFestivo.REGIONAL),
    ]
    db.add_all(festivos_sachsen)
    db.flush()

    # =========================================================
    # --- Periodos de vacaciones 2026 (30 días/año estándar) ---
    # =========================================================
    dias_extra_antiguedad = {
        101: 2,   # Rene — antigüedad desde 2020
        106: 3,   # Klaus — antigüedad desde 2018
        108: 5,   # Jürgen — senior desde 2017
    }
    periodos = []
    for emp in empleados:
        extra = dias_extra_antiguedad.get(emp.id_nummer, 0)
        p = PeriodoVacaciones(
            empleado_id=emp.id,
            anio=2026,
            dias_contrato=30,
            dias_extra=extra,
            notas=f"Vacaciones 2026 — {emp.nombre} {emp.apellido or ''}".strip(),
        )
        periodos.append(p)
        db.add(p)
    db.flush()

    # Mapa empleado_id_nummer → periodo para las solicitudes
    periodo_by_nummer = {emp.id_nummer: p for emp, p in zip(empleados, periodos)}

    # =========================================================
    # --- Solicitudes de vacaciones de ejemplo ---
    # =========================================================

    # 1. Rene Raack — APROBADA (vacaciones verano)
    rene = empleados[0]  # id_nummer 101
    p_rene = periodo_by_nummer[101]
    sol_rene = SolicitudVacaciones(
        empleado_id=rene.id,
        periodo_id=p_rene.id,
        fecha_inicio=date(2026, 7, 6),
        fecha_fin=date(2026, 7, 17),
        dias=10,  # 2 semanas laborables
        tipo_ausencia=TipoAusencia.VACACIONES,
        estado=EstadoSolicitud.APROBADA,
        aprobado_por_nivel1="M.Schmidt (Abteilungsleiter)",
        fecha_nivel1=datetime(2026, 3, 1, 10, 0),
        notas_nivel1="Aprobado — sin solapamiento con otros",
        aprobado_por_nivel2="Admin RRHH",
        fecha_nivel2=datetime(2026, 3, 3, 14, 30),
        notas_nivel2="Confirmado",
        notas="Vacaciones de verano 2026",
    )
    p_rene.dias_usados = 10
    db.add(sol_rene)

    # 2. Anna Schmidt — PROPUESTA (esperando nivel 2)
    anna = empleados[2]  # id_nummer 103
    p_anna = periodo_by_nummer[103]
    sol_anna = SolicitudVacaciones(
        empleado_id=anna.id,
        periodo_id=p_anna.id,
        fecha_inicio=date(2026, 4, 14),
        fecha_fin=date(2026, 4, 22),
        dias=7,
        tipo_ausencia=TipoAusencia.VACACIONES,
        estado=EstadoSolicitud.PROPUESTA,
        aprobado_por_nivel1="K.Hoffmann (Abteilungsleiter)",
        fecha_nivel1=datetime(2026, 3, 5, 9, 0),
        notas_nivel1="Sin problemas — vacaciones Ostern",
        notas="Semana Santa 2026",
    )
    db.add(sol_anna)

    # 3. Thomas Weber — PENDIENTE (recién solicitada)
    thomas = empleados[3]  # id_nummer 104
    p_thomas = periodo_by_nummer[104]
    sol_thomas = SolicitudVacaciones(
        empleado_id=thomas.id,
        periodo_id=p_thomas.id,
        fecha_inicio=date(2026, 5, 25),
        fecha_fin=date(2026, 5, 29),
        dias=5,
        tipo_ausencia=TipoAusencia.VACACIONES,
        estado=EstadoSolicitud.PENDIENTE,
        notas="Fin de semana largo Pfingsten",
    )
    db.add(sol_thomas)

    db.commit()
    db.close()

    print("✅ Seed completado:")
    print(f"   3 grupos, 4 centros de coste, 10 empleados")
    print(f"   Fichajes generados para {min(5, (today - start_of_week).days + 1)} días")
    print(f"   {len(festivos_sachsen)} festivos Sachsen 2026")
    print(f"   10 periodos de vacaciones 2026 creados")
    print(f"   3 solicitudes de vacaciones de ejemplo (APROBADA, PROPUESTA, PENDIENTE)")


if __name__ == "__main__":
    seed()
