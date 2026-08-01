"""Pure calculations for the admin statistics page."""
from __future__ import annotations
import datetime as dt
import json
import math
from collections import Counter, defaultdict

RU_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
RU_WEEKDAY_FULL = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
CANCEL_LABELS = {"passenger":"Пассажир", "driver":"Водитель", "dispatcher":"Диспетчер", "system":"Автоматически", "spam_report":"Жалоба на спам", None:"Не указано", "":"Не указано"}


def _local(value, tz):
    if value is None: return None
    if value.tzinfo is None: value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(tz)


def _money(value):
    try: return float(value or 0)
    except (TypeError, ValueError): return 0.0


def _clock_mean(values):
    if not values: return "—"
    angles=[2*math.pi*((v.hour*3600+v.minute*60+v.second)/86400) for v in values]
    x=sum(math.cos(a) for a in angles); y=sum(math.sin(a) for a in angles)
    if abs(x)<1e-9 and abs(y)<1e-9: return "—"
    minutes=round(((math.atan2(y,x)%(2*math.pi))/(2*math.pi))*1440)%1440
    return f"{minutes//60:02d}:{minutes%60:02d}"


def _duration_minutes(start, end, tz):
    start=_local(start,tz); end=_local(end,tz)
    if not start or not end or end < start: return None
    return (end-start).total_seconds()/60


def _percent_delta(current, previous):
    if previous == 0: return None if current == 0 else 100.0
    return (current-previous)/previous*100


def _declined_ids(raw):
    if not raw: return []
    try:
        value=json.loads(raw)
        if isinstance(value,list): return [int(x) for x in value if str(x).isdigit()]
    except Exception: pass
    return [int(x) for x in str(raw).replace(";",",").split(",") if x.strip().isdigit()]


def _period_metrics(rows, start, end, tz):
    items=[]
    for order in rows:
        created=_local(getattr(order,"created_at",None),tz)
        if created and start <= created < end: items.append((order,created))
    total=len(items)
    completed=[(o,c) for o,c in items if getattr(o,"status",None)=="completed"]
    cancelled=[(o,c) for o,c in items if getattr(o,"status",None)=="cancelled"]
    priced=[_money(o.price) for o,_ in completed if _money(getattr(o,"price",0))>0]
    no_driver=[(o,c) for o,c in items if getattr(o,"status",None)=="no_drivers" or (getattr(o,"status",None)=="cancelled" and not getattr(o,"driver_id",None) and (getattr(o,"decline_count",0) or getattr(o,"last_decline_reason",None) or getattr(o,"cancelled_by",None)=="system"))]
    return {"items":items,"total":total,"completed":len(completed),"completed_rate":len(completed)/total*100 if total else 0,"cancelled":len(cancelled),"cancelled_rate":len(cancelled)/total*100 if total else 0,"no_driver":len(no_driver),"no_driver_rate":len(no_driver)/total*100 if total else 0,"avg_check":sum(priced)/len(priced) if priced else 0,"revenue":sum(priced)}


def build_statistics(orders, bookings, now, tz):
    now=_local(now,tz) or now
    current_start=now-dt.timedelta(days=7); previous_start=now-dt.timedelta(days=14)
    current=_period_metrics(orders,current_start,now,tz)
    previous=_period_metrics(orders,previous_start,current_start,tz)
    items=current["items"]

    created_values=[created for _,created in items]
    hourly=Counter(v.hour for v in created_values)
    hour_buckets=Counter(v.replace(minute=0,second=0,microsecond=0) for v in created_values)
    if hour_buckets:
        peak_start,peak_count=max(hour_buckets.items(),key=lambda x:(x[1],x[0])); peak_label=f"{peak_start:%d.%m, %H:%M}–{(peak_start+dt.timedelta(hours=1)):%H:%M}"
    else: peak_count=0; peak_label="—"

    day_dates=[now.date()-dt.timedelta(days=n) for n in range(6,-1,-1)]
    day_counts=Counter(v.date() for v in created_values); day_max=max([day_counts[d] for d in day_dates]+[1])
    daily=[{"label":f"{RU_WEEKDAYS[d.weekday()]} {d:%d.%m}","count":day_counts[d],"percent":day_counts[d]/day_max*100} for d in day_dates]
    hourly_max=max(list(hourly.values())+[1]); hourly_chart=[{"hour":h,"count":hourly[h],"percent":hourly[h]/hourly_max*100} for h in range(24)]

    accepted_hours=defaultdict(set)
    for o,_ in items:
        accepted=_local(getattr(o,"driver_accept_time",None),tz)
        if accepted and current_start<=accepted<now and getattr(o,"driver_id",None): accepted_hours[accepted.replace(minute=0,second=0,microsecond=0)].add(o.driver_id)
    avg_drivers=sum(map(len,accepted_hours.values()))/len(accepted_hours) if accepted_hours else 0

    search_times=[]; ride_times=[]
    for o,created in items:
        value=_duration_minutes(created,getattr(o,"driver_accept_time",None),tz)
        if value is not None: search_times.append(value)
        ride_start=getattr(o,"driver_departed_at",None) or getattr(o,"driver_accept_time",None)
        value=_duration_minutes(ride_start,getattr(o,"completed_at",None),tz)
        if value is not None: ride_times.append(value)

    cancellations=Counter(CANCEL_LABELS.get(getattr(o,"cancelled_by",None),str(getattr(o,"cancelled_by",None) or "Не указано")) for o,_ in items if getattr(o,"status",None)=="cancelled")
    cancel_total=sum(cancellations.values()) or 1
    cancellation_rows=[{"label":label,"count":count,"percent":count/cancel_total*100} for label,count in cancellations.most_common()]

    type_counts=Counter("Доставка" if getattr(o,"order_type",None)=="delivery" else "Обычная поездка" for o,_ in items)
    current_bookings=[]
    for b in bookings:
        created=_local(getattr(b,"created_at",None),tz)
        if created and current_start<=created<now: current_bookings.append(b)
    type_counts["Бронь"]+=len(current_bookings)
    type_total=sum(type_counts.values()) or 1
    type_rows=[{"label":label,"count":count,"percent":count/type_total*100} for label,count in type_counts.most_common()]

    type_prices=defaultdict(list)
    for o,_ in items:
        if getattr(o,"status",None)=="completed" and _money(getattr(o,"price",0))>0: type_prices["Доставка" if getattr(o,"order_type",None)=="delivery" else "Обычная поездка"].append(_money(o.price))
    avg_checks=[{"label":label,"value":sum(vals)/len(vals),"count":len(vals)} for label,vals in type_prices.items()]

    weekday=Counter(v.weekday() for v in created_values)
    busiest_weekday=RU_WEEKDAY_FULL[max(weekday,key=weekday.get)] if weekday else "—"
    busiest_weekday_count=max(weekday.values()) if weekday else 0

    routes=Counter()
    for o,_ in items:
        route=(getattr(o,"route_text",None) or f"{getattr(o,'address_from','')} → {getattr(o,'address_to','')}").strip()
        if route: routes[route]+=1
    popular_routes=[{"label":label[:110],"count":count} for label,count in routes.most_common(10)]

    driver_rows={}; declines=Counter()
    for o,_ in items:
        for driver_id in _declined_ids(getattr(o,"declined_driver_ids",None)): declines[driver_id]+=1
        if getattr(o,"status",None)!="completed" or not getattr(o,"driver_id",None): continue
        did=o.driver_id; driver=getattr(o,"driver",None)
        row=driver_rows.setdefault(did,{"id":did,"name":getattr(driver,"full_name",None) or f"ID {did}","completed":0,"revenue":0.0,"rating":getattr(driver,"rating",0) if driver else 0})
        row["completed"]+=1; row["revenue"]+=_money(getattr(o,"price",0))
    for did,row in driver_rows.items(): row["avg_check"]=row["revenue"]/row["completed"] if row["completed"] else 0; row["declines"]=declines[did]
    top_drivers=sorted(driver_rows.values(),key=lambda x:(x["completed"],x["revenue"]),reverse=True)[:10]

    comparisons=[]
    for label,key,suffix in (("Заявки","total",""),("Выполнение","completed_rate"," п.п."),("Средний чек","avg_check"," ₽"),("Выручка","revenue"," ₽"),("Без водителя","no_driver_rate"," п.п.")):
        cur=current[key]; prev=previous[key]
        delta=(cur-prev) if key.endswith("rate") else _percent_delta(cur,prev)
        comparisons.append({"label":label,"current":cur,"previous":prev,"delta":delta,"suffix":suffix,"is_percent":key.endswith("rate")})

    return {**{k:v for k,v in current.items() if k!="items"},"period_start":current_start.strftime("%d.%m.%Y %H:%M"),"period_end":now.strftime("%d.%m.%Y %H:%M"),"avg_drivers_per_hour":avg_drivers,"avg_order_time":_clock_mean(created_values),"avg_orders_per_day":current["total"]/7,"peak_label":peak_label,"peak_count":peak_count,"avg_search_minutes":sum(search_times)/len(search_times) if search_times else 0,"median_search_minutes":sorted(search_times)[len(search_times)//2] if search_times else 0,"avg_ride_minutes":sum(ride_times)/len(ride_times) if ride_times else 0,"busiest_weekday":busiest_weekday,"busiest_weekday_count":busiest_weekday_count,"daily":daily,"hourly":hourly_chart,"cancellations":cancellation_rows,"types":type_rows,"avg_checks":avg_checks,"popular_routes":popular_routes,"top_drivers":top_drivers,"comparisons":comparisons,"booking_count":len(current_bookings),"online_history_available":False}


DRIVER_DAY_LABELS = ("Позавчера", "Вчера", "Сегодня")


def build_driver_day_stats(orders, now, tz):
    """Completed requests per driver for the day before yesterday, yesterday
    and today. Rows are sorted from the highest total to the lowest."""
    today = _local(now, tz).date()
    days = [today - dt.timedelta(days=2), today - dt.timedelta(days=1), today]
    index = {day: position for position, day in enumerate(days)}
    rows = {}
    for order in orders:
        if getattr(order, "status", None) != "completed":
            continue
        driver = getattr(order, "driver", None)
        driver_id = getattr(order, "driver_id", None) or getattr(driver, "id", None)
        if not driver_id:
            continue
        moment = _local(getattr(order, "completed_at", None) or getattr(order, "created_at", None), tz)
        if not moment or moment.date() not in index:
            continue
        row = rows.get(driver_id)
        if row is None:
            row = {
                "driver_id": driver_id,
                "name": (getattr(driver, "full_name", None) or "id%s" % (getattr(driver, "vk_id", "") or driver_id)),
                "vk_id": getattr(driver, "vk_id", None),
                "car": getattr(driver, "car_full", None) or "—",
                "counts": [0, 0, 0],
                "revenue": 0.0,
                "total": 0,
            }
            rows[driver_id] = row
        row["counts"][index[moment.date()]] += 1
        row["total"] += 1
        row["revenue"] += _money(getattr(order, "price", 0))
    result = sorted(
        rows.values(),
        key=lambda item: (-item["total"], -item["counts"][2], item["name"].lower()),
    )
    for position, row in enumerate(result, start=1):
        row["place"] = position
    totals = [sum(row["counts"][i] for row in result) for i in range(3)]
    return {
        "days": [
            {"label": DRIVER_DAY_LABELS[i], "date": days[i].strftime("%d.%m.%Y"), "count": totals[i]}
            for i in range(3)
        ],
        "rows": result,
        "totals": totals,
        "total": sum(totals),
        "revenue": sum(row["revenue"] for row in result),
        "drivers": len(result),
    }
