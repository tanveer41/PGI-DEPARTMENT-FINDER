from flask import Flask, render_template, request, jsonify
from rapidfuzz import fuzz, process
import urllib.parse
import re
import csv
import os
import logging
from logging.handlers import RotatingFileHandler

app = Flask(__name__)
application = app  # PythonAnywhere WSGI requirement

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change_this_in_prod")
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Logging setup
if not app.debug:
    handler = RotatingFileHandler('app.log', maxBytes=1024*1024*10, backupCount=5)
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)

# ====================
# AEC Data Load
# ====================
aec_data = []
def load_aec_data():
    global aec_data
    csv_path = os.path.join(app.root_path, 'aec_data.csv')
    aec_data.clear()
    try:
        if not os.path.exists(csv_path):
            app.logger.error("AEC data file not found")
            return

        with open(csv_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                aec_data.append({
                    "floor": row["Floor"],
                    "block_area": row["Block/Area"],
                    "room_counter_no": row["Room/Counter No."],
                    "operating_days": row["Operating Days"],
                    "department": row["Department/Service"],
                    "notes": row["Notes"]
                })
        app.logger.info(f"Loaded {len(aec_data)} AEC records")
    except Exception as e:
        app.logger.error(f"Failed to load AEC data: {e}")

# ====================
# PGI Data Load
# ====================
pgi_data = []
FLOOR_MAP = {
    "GROUND FLOOR": 0, "GF": 0, "G": 0,
    "FIRST FLOOR": 1, "1ST": 1, "1": 1,
    "SECOND FLOOR": 2, "2ND": 2, "2": 2,
    "THIRD FLOOR": 3, "3RD": 3, "3": 3,
    "FOURTH FLOOR": 4, "4TH": 4, "4": 4,
    "FIFTH FLOOR": 5, "5TH": 5, "5": 5,
    "LEVEL II": 2
}

def load_pgi_data():
    global pgi_data
    path = os.path.join(app.root_path, 'pgi_departments.csv')
    pgi_data.clear()
    try:
        if not os.path.exists(path):
            app.logger.error("PGI data file not found")
            return

        with open(path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                floor_str = str(row.get("Floor", "")).strip().upper()
                pgi_data.append({
                    "level": FLOOR_MAP.get(floor_str),
                    "original_floor_text": floor_str,
                    "room_no": row.get("Room_Numbers", "").strip(),
                    "block": row.get("Building", "").strip(),
                    "days": row.get("Operating_Days", "").strip(),
                    "building": row.get("Building", "").strip(),
                    "department": row.get("Department", "").strip(),
                    "notes": f"{row.get('Special_Timings', '').strip()} | {row.get('Additional_Info', '').strip()}",
                    "opd_type": row.get("OPD_Type", "").strip().lower(),
                    "doctors": row.get("Doctors", "").strip(),
                    "counters": row.get("Counters", "").strip()
                })
        app.logger.info(f"Loaded {len(pgi_data)} PGI records")
    except Exception as e:
        app.logger.error(f"Failed to load PGI data: {e}")

# ====================
# Common Utilities
# ====================
def sanitize_query(q):
    return re.sub(r'[^a-zA-Z0-9\s\-.,&:;\'\"()/A-Za-z0-9]', '', str(q))[:100] if q else ""

def search_aec(query):
    query = query.strip().upper()
    results = []
    suggestion = None
    if not query or not aec_data:
        return results, suggestion

    # Exact match
    for entry in aec_data:
        dept = entry["department"].upper()
        notes = entry["notes"].upper() if entry["notes"] else ''
        if dept == query or dept.replace('.', '') == query.replace('.', '') or \
           notes == query or notes.replace('.', '') == query.replace('.', ''):
            results.append(entry)
    if results:
        return results, suggestion

    # Whole word match
    pattern = r'\b' + re.escape(query.replace('.', '')) + r'\b'
    for entry in aec_data:
        if re.search(pattern, entry["department"].upper().replace('.', '')) or \
           (entry["notes"] and re.search(pattern, entry["notes"].upper().replace('.', ''))):
            results.append(entry)
    if results:
        return results, suggestion

    # Substring
    for entry in aec_data:
        if query in entry["department"].upper() or \
           (entry["notes"] and query in entry["notes"].upper()):
            results.append(entry)
    if results:
        return results, suggestion

    # Fuzzy suggestion
    all_searchable = list(set([e["department"] for e in aec_data] +
                         [e["notes"] for e in aec_data if e["notes"]]))
    match = process.extractOne(query, all_searchable, scorer=fuzz.token_set_ratio)
    if match and match[1] > 70:
        suggestion = match[0]
    return results, suggestion

def search_pgi(query: str) -> tuple[list, str]:
    """Enhanced search that handles numbers, counters, and partial matches"""
    if not query or not pgi_data:
        return [], ""

    query = query.strip()
    query_lower = query.lower()
    results = []
    suggestion = ""

    # Special handling for numeric queries (counters, room numbers)
    if any(char.isdigit() for char in query):
        query_numbers = [int(s) for s in re.findall(r'\d+', query)]

        for entry in pgi_data:
            # Check counter numbers
            counters = entry.get("counters", "")
            if counters:
                counter_parts = []
                for part in counters.split(','):
                    part = part.strip()
                    if '-' in part:
                        try:
                            start, end = part.split('-')
                            if start.isdigit() and end.isdigit():
                                counter_parts.extend(range(int(start), int(end)+1))
                        except ValueError:
                            continue
                    elif part.replace('A', '').isdigit():
                        counter_parts.append(int(part.replace('A', '')))

                if any(q_num in counter_parts for q_num in query_numbers):
                    results.append(entry)
                    continue

            # Check room numbers
            room_no = entry.get("room_no", "")
            if room_no:
                room_numbers = [int(s) for s in re.findall(r'\d+', room_no)]
                if any(q_num in room_numbers for q_num in query_numbers):
                    results.append(entry)
                    continue

            # Check department name numbers
            dept_name_numbers = [int(s) for s in re.findall(r'\d+', entry.get("department", ""))]
            if any(q_num in dept_name_numbers for q_num in query_numbers):
                results.append(entry)

    # Normal text search
    if not results:
        # Phase 1: Exact match
        results = [
            entry for entry in pgi_data
            if query_lower in entry.get("department", "").lower()
        ]

        # Phase 2: Partial match
        if not results:
            search_fields = ["department", "doctors", "notes", "block", "building"]
            for entry in pgi_data:
                for field in search_fields:
                    field_value = str(entry.get(field, "")).lower()
                    if query_lower in field_value:
                        results.append(entry)
                        break

        # Phase 3: Fuzzy match
        if not results and len(query) > 2:
            department_names = [e["department"] for e in pgi_data if e.get("department")]
            best_match = process.extractOne(query, department_names, scorer=fuzz.token_set_ratio)
            if best_match and best_match[1] > 65:
                suggestion = best_match[0]

    return results, suggestion

# ====================
# Routes
# ====================
@app.route('/')
def home():
    return render_template('main.html')

@app.route('/aec_index.html', methods=['GET', 'POST'])
def aec_search():
    raw = request.form.get('search_query') if request.method == 'POST' else request.args.get('search_query', '')
    q = sanitize_query(raw)
    results, suggestion = search_aec(q)

    if request.headers.get("Accept") == "application/json":
        return jsonify({
            'department_results': results,
            'navigation_results': [],
            'building_results': [],
            'suggestion': suggestion
        })

    return render_template('aec_index.html',
                         query=raw,
                         results=results,
                         suggestion=suggestion,
                         hospital='aec')

@app.route('/index.html', methods=['GET', 'POST'])
def pgi_search():
    raw = request.form.get('search_query') if request.method == 'POST' else request.args.get('search_query', '')
    q = sanitize_query(raw)
    results, suggestion = search_pgi(q)

    if request.headers.get("Accept") == "application/json":
        return jsonify({
            'department_results': results,
            'navigation_results': [],
            'building_results': [],
            'suggestion': suggestion
        })

    return render_template('index.html',
                         query=raw,
                         department_results=results,
                         navigation_results=[],
                         building_results=[],
                         suggestion=suggestion,
                         hospital='pgi')

@app.route('/test')
def test():
    return "✔ Flask app is working!"

# ====================
# Init
# ====================
def initialize_app():
    load_aec_data()
    load_pgi_data()
    app.logger.info("✔ Data loaded successfully")

initialize_app()

if __name__ == '__main__':
    app.run(debug=True)