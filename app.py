import os
import calendar
import logging
from datetime import datetime
from collections import defaultdict

from flask import Flask, redirect, url_for, session, request, render_template
from markupsafe import Markup
from dotenv import load_dotenv
import pymongo
import gspread
import pytz
from pytz import timezone

from util import is_number, parse_date

load_dotenv()

app = Flask(__name__)

app.secret_key = os.environ['SECRET_KEY']

MONGO_CONNECTION_STRING = os.environ['MONGO_CONNECTION_STRING']
MONGO_DB_NAME = os.environ['MONGO_DB_NAME']
MONGO_CLIENT = pymongo.MongoClient(MONGO_CONNECTION_STRING)
MONGO_DB = MONGO_CLIENT[MONGO_DB_NAME]
CLEANUPS_COLLECTION = MONGO_DB['Cleanups']
REPORTS_COLLECTION = MONGO_DB['Reports']
UPDATE_COLLECTION = MONGO_DB['Update']

GOOGLE_SERVICE_ACCOUNT_CREDENTIALS = {
        'type': 'service_account',
        'project_id': os.environ['project_id'],
        'private_key_id': os.environ['private_key_id'],
        'private_key': os.environ['private_key'].replace('\\n', '\n'),
        'client_email': os.environ['client_email'],
        'client_id': os.environ['client_id'],
        'auth_uri': os.environ['auth_uri'],
        'token_uri': os.environ['token_uri'],
        'auth_provider_x509_cert_url': os.environ['auth_provider_x509_cert_url'],
        'client_x509_cert_url': os.environ['client_x509_cert_url']
    }
GOOGLE_SPREAD = gspread.service_account_from_dict(GOOGLE_SERVICE_ACCOUNT_CREDENTIALS)
GOOGLE_SHEET_NAME = 'Watershed Brigade - Master Tracking Sheet'
GOOGLE_SHEET = GOOGLE_SPREAD.open(GOOGLE_SHEET_NAME)

def get_cleanups():
    """
    Retrieves, cleans, and validates cleanup data from Santa Barbara Channelkeeper's Google Sheet.
    Stores data in MongoDB for backup purposes if it has not been updated within the currented day.
    """
    cleanups = []
    updateMongo = True
    if datetime.now().strftime('%d') == UPDATE_COLLECTION.find_one().get('last_day_updated'):
        updateMongo = False

    try:
        # Try opening sheet corresponding to current year, otherwise use a default year
        utc_year = datetime.now().strftime('%Y')
        try:
            wsheet = GOOGLE_SHEET.worksheet(utc_year + ' WB Tracking') 
        except:
            wsheet = GOOGLE_SHEET.worksheet('2021 WB Tracking')
        raw_data = wsheet.get_all_values()

        # Clean, validate, and transform each row
        cleanups = []
        for row in raw_data:
            _, _, name, group_size, date, location, _, _, weight, hours, combined_hours, p1, p2, p3, p4, p5, points, coords = row[:18]
            if not name or not location:
                continue
            if not is_number(group_size) or not is_number(weight) or not is_number(hours) or not is_number(combined_hours):
                continue
            if not date.count('/') == 2:
                continue
            if coords.count(',') != 1:
                continue
            x, y = coords.split(",")
            if not is_number(x) or not is_number(y):
                continue
            points = max(sum(float(p) for p in [p1, p2, p3, p4, p5] if is_number(p)), float(points) if is_number(points) else 0)
            if not points:
                continue
            try:
                month, day, year = parse_date(date)
            except:
                continue

            cleanups.append({
                "name": name, 
                "group_size": int(group_size), 
                "date": datetime(year, month, day), 
                "location": location,
                "weight": float(weight),
                "hours": float(hours),
                "combined_hours": float(combined_hours),
                "points": float(points),
                "coords": coords
            })
        
        cleanups.sort(key=lambda x: x["date"])

        # Backup data to MongoDB and reset the day timer
        if updateMongo:
            UPDATE_COLLECTION.delete_many({})
            UPDATE_COLLECTION.insert_one({'last_day_updated': datetime.now().strftime('%d')})
            CLEANUPS_COLLECTION.delete_many({})
            CLEANUPS_COLLECTION.insert_many(cleanups)
    except Exception as e:
        logging.exception("Failed to parse new cleanup data.")
        cleanups = list(CLEANUPS_COLLECTION.find({}).sort("date", 1))
    return cleanups
            
@app.route('/') 
def render_maps():
    """
    Render the maps page.
    """
    cleanups = get_cleanups()

    # Create a checkbox for each month so they can be toggled on/off from map
    checkboxes_html = ''
    months = list(set(map(lambda cleanup: cleanup["date"].month, cleanups)))
    month_names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    for month in months:
        month_name = month_names[month-1]
        checkboxes_html += f'''
        <label class="checkbox-inline">
            <input type="checkbox" value="{month_name}" class="Month" id="{month_name}" checked />
            {month_name}
        </label>
        '''
        
    data_report = []
    cursor = REPORTS_COLLECTION.find({})
    for item in cursor:
        data_report.append([item.get('0'), item.get('1'), item.get('2'), item.get('3'), item.get('4'), item.get('5')])
    reports = 0
    disable = ''
    report_limit = ''
    date_now = datetime.now(tz=pytz.utc)
    date_now = date_now.astimezone(timezone('America/Los_Angeles'))
    resolve_locations = ''
    for row in data_report: #if the number of reports made on the current date exceed ten, then disable the reports form.
        if row[4] == date_now.strftime('%m/%d/%Y'):
            reports += 1
        if '/' in row[4] and row[3] == 'Not resolved': #generates options for the resolve form.
            resolve_locations += '<option>' + row[0] + '</option>'
    if reports >= 10:
        report_limit = '<p id="report-limit">The maximum number of reports have been reached, please try tomorrow.</p>'
        disable = 'disabled'
    if len(data_report) > 40: #if there are more than 40 reports already, change location question so that it only accepts coordinates. This is to prevent geocoding limits.
        location_question = '<label>Coordinates: ( <input name="x-location" class="form-control" placeholder="34.011761" maxlength="10" type="number" step="0.000001" required> , <input name="y-location" class="form-control" placeholder="-119.777489" maxlength="10" type="number" step="0.000001" required> )</label>'
    else:
        location_question = '<label for="location">Specific Address/Coordinates:&nbsp;</label><input type="text" class="form-control" id="location" maxlength="40" name="location" required>'
    returner = ''
    if 'returner' not in session:
        returner = '<script>$(document).ready(function() { $("#myModal").modal("show");});</script>'
        session['returner'] = 'yes'
    return render_template('maps.html', 
                           checkboxes = Markup(checkboxes_html), 
                           location_question = Markup(location_question), 
                           report_limit = Markup(report_limit), 
                           submit = Markup(disable), 
                           resolve_locations = Markup(resolve_locations), 
                           returner = Markup(returner))

@app.route('/maps-embed')
def render_maps_embed():
    """
    Render the maps widget for embedding into an iframe.
    Essentially the same as render_maps().
    """
    cleanups = get_cleanups()

    # Create a checkbox for each month so they can be toggled on/off from map
    checkboxes_html = ''
    months = list(set(map(lambda cleanup: cleanup["month"], cleanups)))
    month_names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    for month in months:
        month_name = month_names[month-1]
        checkboxes_html += f'''
        <label class="checkbox-inline">
            <input type="checkbox" value="{month_name}" class="Month" id="{month_name}" checked />
            {month_name}
        </label>
        '''
    
    data_report = []
    cursor = REPORTS_COLLECTION.find({})
    for item in cursor:
        data_report.append([item.get('0'), item.get('1'), item.get('2'), item.get('3'), item.get('4'), item.get('5')])
    reports = 0
    disable = ''
    report_limit = ''
    date_now = datetime.now(tz=pytz.utc)
    date_now = date_now.astimezone(timezone('America/Los_Angeles'))
    resolve_locations = ''
    for row in data_report: #if the number of reports made on the current date exceed ten, then disable the reports form.
        if row[4] == date_now.strftime('%m/%d/%Y'):
            reports += 1
        if '/' in row[4] and row[3] == 'Not resolved': #generates options for the resolve form.
            resolve_locations += '<option>' + row[0] + '</option>'
    if reports >= 10:
        report_limit = '<p id="report-limit">The maximum number of reports have been reached, please try tomorrow.</p>'
        disable = 'disabled'
    if len(data_report) > 40: #if there are more than 40 reports already, change location question so that it only accepts coordinates. This is to prevent geocoding limits.
        location_question = '<label>Coordinates: ( <input name="x-location" class="form-control" placeholder="34.011761" maxlength="10" type="number" step="0.000001" required> , <input name="y-location" class="form-control" placeholder="-119.777489" maxlength="10" type="number" step="0.000001" required> )</label>'
    else:
        location_question = '<label for="location">Specific Address/Coordinates:&nbsp;</label><input type="text" class="form-control" id="location" maxlength="40" name="location" required>'
    return render_template('maps-embed.html', checkboxes = Markup(checkboxes_html), location_question = Markup(location_question), report_limit = Markup(report_limit), submit = disable)

@app.route('/ranks')
def render_ranks():
    '''
    Renders the ranks page.
    '''
    cleanups = get_cleanups()

    latest_year = cleanups[-1]["date"].year
    latest_month = cleanups[-1]["date"].month

    # Create descending point scoreboards for latest month and all time
    month_scoreboard = defaultdict(int)
    total_scoreboard = defaultdict(int)
    for cleanup in cleanups:
        if not cleanup["points"]:
            continue
        if cleanup["date"].month == latest_month and cleanup["date"].year == latest_year:
            month_scoreboard[cleanup["name"]] += cleanup["points"]
        total_scoreboard[cleanup["name"]] += cleanup["points"]
    
    month_scoreboard = sorted(month_scoreboard.items(), key=lambda x: x[1], reverse=True)
    total_scoreboard = sorted(total_scoreboard.items(), key=lambda x: x[1], reverse=True)

    first_place_month, second_place_month, third_place_month = '', '', ''
    first_score_month, second_score_month, third_score_month = '', '', ''
    first_place_total, second_place_total, third_place_total = '', '', ''
    first_score_total, second_score_total, third_score_total = '', '', ''
    if len(month_scoreboard) >= 1:
        first_place_month = month_scoreboard[0][0]
        first_score_month = str(round(month_scoreboard[0][1]))
    if len(month_scoreboard) >= 2:
        second_place_month = month_scoreboard[1][0]
        second_score_month = str(round(month_scoreboard[1][1]))
    if len(month_scoreboard) >= 3:
        third_place_month = month_scoreboard[2][0]
        third_score_month = str(round(month_scoreboard[2][1]))
    if len(total_scoreboard) >= 1:
        first_place_total = total_scoreboard[0][0]
        first_score_total = str(round(total_scoreboard[0][1]))
    if len(total_scoreboard) >= 2:
        second_place_total = total_scoreboard[1][0]
        second_score_total = str(round(total_scoreboard[1][1]))
    if len(total_scoreboard) >= 3:
        third_place_total = total_scoreboard[2][0]
        third_score_total = str(round(total_scoreboard[2][1]))
    
    scoreboard_month_html = ''
    scoreboard_total_html = ''
    rank = 4
    while rank <= len(month_scoreboard):
        name = month_scoreboard[rank - 1][0]
        points = round(month_scoreboard[rank - 1][1])
        scoreboard_month_html += f'''
        <tr>
            <td>
                <div class="rankings-bottom">
                    <div class="name">
                        <p>{rank}. {name}</p>
                    </div>
                    <div class="points">
                        <p><b>{points}</b></p>
                    </div>
                </div>
            </td>
        </tr>
        '''
        rank += 1
    rank = 4
    while rank <= len(total_scoreboard):
        name = total_scoreboard[rank - 1][0]
        points = round(total_scoreboard[rank - 1][1])
        scoreboard_total_html += f'''
        <tr>
            <td>
                <div class="rankings-bottom">
                    <div class="name">
                        <p>{rank}. {name}</p>
                    </div>
                    <div class="points">
                        <p><b>{points}</b></p>
                    </div>
                </div>
            </td>
        </tr>
        '''
        rank += 1
    return render_template('ranks.html', 
                           first_place_month = first_place_month, 
                           second_place_month = second_place_month, 
                           third_place_month = third_place_month, 
                           first_score_month = first_score_month, 
                           second_score_month = second_score_month, 
                           third_score_month = third_score_month, 
                           scoreboard_month_html = Markup(scoreboard_month_html), 
                           first_place_total = first_place_total, 
                           second_place_total = second_place_total, 
                           third_place_total = third_place_total, 
                           first_score_total = first_score_total, 
                           second_score_total = second_score_total, 
                           third_score_total = third_score_total, 
                           scoreboard_total_html = Markup(scoreboard_total_html))

@app.route('/ranks-embed')
def render_ranks_embed():
    '''
    Render the ranks widget for embedding into an iframe.
    Essentially the same as render_ranks().
    '''
    cleanups = get_cleanups()

    latest_year = cleanups[-1]["date"].year
    latest_month = cleanups[-1]["date"].month

    # Create descending point scoreboards for latest month and all time
    month_scoreboard = defaultdict(int)
    total_scoreboard = defaultdict(int)
    for cleanup in cleanups:
        if not cleanup["points"]:
            continue
        if cleanup["date"].month == latest_month and cleanup["date"].year == latest_year:
            month_scoreboard[cleanup["name"]] += cleanup["points"]
        total_scoreboard[cleanup["name"]] += cleanup["points"]
    
    month_scoreboard = sorted(month_scoreboard.items(), key=lambda x: x[1], reverse=True)
    total_scoreboard = sorted(total_scoreboard.items(), key=lambda x: x[1], reverse=True)

    first_place_month, second_place_month, third_place_month = '', '', ''
    first_score_month, second_score_month, third_score_month = '', '', ''
    first_place_total, second_place_total, third_place_total = '', '', ''
    first_score_total, second_score_total, third_score_total = '', '', ''
    if len(month_scoreboard) >= 1:
        first_place_month = month_scoreboard[0][0]
        first_score_month = str(round(month_scoreboard[0][1]))
    if len(month_scoreboard) >= 2:
        second_place_month = month_scoreboard[1][0]
        second_score_month = str(round(month_scoreboard[1][1]))
    if len(month_scoreboard) >= 3:
        third_place_month = month_scoreboard[2][0]
        third_score_month = str(round(month_scoreboard[2][1]))
    if len(total_scoreboard) >= 1:
        first_place_total = total_scoreboard[0][0]
        first_score_total = str(round(total_scoreboard[0][1]))
    if len(total_scoreboard) >= 2:
        second_place_total = total_scoreboard[1][0]
        second_score_total = str(round(total_scoreboard[1][1]))
    if len(total_scoreboard) >= 3:
        third_place_total = total_scoreboard[2][0]
        third_score_total = str(round(total_scoreboard[2][1]))
    
    scoreboard_month_html = ''
    scoreboard_total_html = ''
    rank = 4
    while rank <= len(month_scoreboard):
        name = month_scoreboard[rank - 1][0]
        points = round(month_scoreboard[rank - 1][1])
        scoreboard_month_html += f'''
        <tr>
            <td>
                <div class="rankings-bottom">
                    <div class="name">
                        <p>{rank}. {name}</p>
                    </div>
                    <div class="points">
                        <p><b>{points}</b></p>
                    </div>
                </div>
            </td>
        </tr>
        '''
        rank += 1
    rank = 4
    while rank <= len(total_scoreboard):
        name = total_scoreboard[rank - 1][0]
        points = round(total_scoreboard[rank - 1][1])
        scoreboard_total_html += f'''
        <tr>
            <td>
                <div class="rankings-bottom">
                    <div class="name">
                        <p>{rank}. {name}</p>
                    </div>
                    <div class="points">
                        <p><b>{points}</b></p>
                    </div>
                </div>
            </td>
        </tr>
        '''
        rank += 1
    return render_template('ranks-embed.html', 
                           first_place_month = first_place_month, 
                           second_place_month = second_place_month, 
                           third_place_month = third_place_month, 
                           first_score_month = first_score_month, 
                           second_score_month = second_score_month, 
                           third_score_month = third_score_month, 
                           scoreboard_month_html = Markup(scoreboard_month_html), 
                           first_place_total = first_place_total, 
                           second_place_total = second_place_total, 
                           third_place_total = third_place_total, 
                           first_score_total = first_score_total, 
                           second_score_total = second_score_total, 
                           third_score_total = third_score_total, 
                           scoreboard_total_html = Markup(scoreboard_total_html))

@app.route('/stats')
def render_stats():
    """
    Renders the statistics page.
    """
    cleanups = get_cleanups()

    # For table:
    total_trash_per_month_year = defaultdict(float)
    total_volunteers_per_month_year = defaultdict(int)
    total_time_per_month_year = defaultdict(float)
    total_sites_per_month_year = defaultdict(int)

    # For graphs:
    total_trash_monthly, total_time_monthly = [[0.0] * 12 for _ in range(2)]
    total_volunteers_monthly, total_cleanups_monthly = [[0] * 12 for _ in range(2)]

    visited_locations = []
    month_years = []
    current_month = None
    dot_graph_points = defaultdict(str)
    max_combined_hours = 0

    for cleanup in cleanups:
        month_year = cleanup["date"].strftime("%B %Y").upper()
        month = cleanup["date"].month
        weight, group_size, combined_hours = cleanup["weight"], cleanup["group_size"], cleanup["combined_hours"]
        x_coord, y_coord = map(float, cleanup["coords"].split(","))

        total_trash_per_month_year[month_year] += weight
        total_volunteers_per_month_year[month_year] += group_size
        total_time_per_month_year[month_year] += combined_hours

        total_trash_monthly[month - 1] += weight
        total_volunteers_monthly[month - 1] += group_size
        total_time_monthly[month - 1] += combined_hours
        total_cleanups_monthly[month - 1] += 1

        if current_month != month:
            current_month = month
            month_years.append(month_year)
            visited_locations = []

        # Assign dot color on dot graph based on group size.
        if group_size <= 1:
            color = '#ffb600'
        elif group_size <= 5:
            color = '#ff9900'
        elif group_size <= 10:
            color = '#ff7900'
        elif group_size <= 20:
            color = '#ff5200'
        else:
            color = '#ff0000'

        dot_graph_points[group_size] += '{ x: ' + str(combined_hours) + ', y: ' + str(weight) + ', color: "' + color + '" },'
        if combined_hours > max_combined_hours:
            max_combined_hours = combined_hours

        # If cleanups are near each other within the same month, consider them in one location 
        nearby = False
        for x, y in visited_locations:
            if x > x_coord - 0.002 and x < x_coord + 0.002 and y > y_coord - 0.002 and y < y_coord + 0.002:
                nearby = True
        if not nearby:
            total_sites_per_month_year[month_year] += 1
            visited_locations.append((x_coord, y_coord))

    table_rows_html = ''
    for month_year in month_years:
        if total_trash_per_month_year[month_year] and total_volunteers_per_month_year[month_year] and total_sites_per_month_year[month_year]:
            table_rows_html += f'''
            <tr>
                <td class="cell no-bold">{month_year}</td>
                <td class="cell">{total_sites_per_month_year[month_year]}</td>
                <td class="cell">{total_volunteers_per_month_year[month_year]}</td>
                <td class="cell">{round(total_trash_per_month_year[month_year], 1)}</td>
            </tr>
            '''
        else:
            table_rows_html += f'''
            <tr>
                <td class="cell no-bold">{month_year}</td>
                <td class="cell"></td>
                <td class="cell"></td>
                <td class="cell"></td>
            </tr>
            '''

    dot_graph_data = []
    dot_graph_trend_line_data = []
    for i, group_size in enumerate(dot_graph_points):
        dot_graph_points[group_size] = dot_graph_points.get(group_size)[:-1]
        dot_graph_data.append('{' +
                'type: "scatter",' +
                'name: "' + str(group_size) + ' Person Group",' +
                'indexLabelFontSize: 16,' +
                'toolTipContent: "<span style=\\"color:#4F81BC \\"><b>{name}</b></span><br/><b> Time: </b> {x} hrs<br/><b> Weight of Trash </b></span> {y} lbs",' +
                'dataPoints: [' +
                dot_graph_points[group_size] +
            ']}'
        )
        # Javascript that concats each group of data to calculate the trend line.
        if i < len(dot_graph_points) - 1:
            dot_graph_trend_line_data.append('chart.data[' + str(i + 1) + '].dataPoints')
    dot_graph_data = ','.join(dot_graph_data)
    dot_graph_trend_line_data = ','.join(dot_graph_trend_line_data)

    histogram_weight = []
    histogram_persons = []
    histogram_time = []
    for i in range(0, 11):
        if total_trash_monthly[i] and total_cleanups_monthly[i] and total_volunteers_monthly[i] and total_time_monthly[i]:
            histogram_weight.append('{ label: "' + calendar.month_name[i+1].upper() + '", y: ' + str(total_trash_monthly[i]/total_cleanups_monthly[i]) + ' }')
            histogram_persons.append('{ label: "' + calendar.month_name[i+1].upper() + '", y: ' + str(total_volunteers_monthly[i]/total_cleanups_monthly[i]) + ' }')
            histogram_time.append('{ label: "' + calendar.month_name[i+1].upper() + '", y: ' + str(total_time_monthly[i]/total_cleanups_monthly[i]) + ' }')
    histogram_weight = ','.join(histogram_weight)
    histogram_persons = ','.join(histogram_persons)
    histogram_time = ','.join(histogram_time)

    return render_template('stats.html',
                           table = Markup(table_rows_html),
                           chart = Markup(dot_graph_data),
                           trend_line = Markup(dot_graph_trend_line_data),
                           end_point = Markup(max_combined_hours),
                           histogram_weight = Markup(histogram_weight),
                           histogram_persons = Markup(histogram_persons),
                           histogram_time = Markup(histogram_time))

@app.route('/stats-embed')
def render_stats_embed():
    """
    Render the stats widget for embedding into an iframe.
    Essentially the same as render_stats() without graphs.
    """
    cleanups = get_cleanups()

    # For table:
    total_trash_per_month_year = defaultdict(float)
    total_volunteers_per_month_year = defaultdict(int)
    total_time_per_month_year = defaultdict(float)
    total_sites_per_month_year = defaultdict(int)

    visited_locations = []
    month_years = []
    current_month = None

    for cleanup in cleanups:
        month_year = cleanup["date"].strftime("%B %Y").upper()
        month = cleanup["date"].month
        weight, group_size, combined_hours = cleanup["weight"], cleanup["group_size"], cleanup["combined_hours"]
        x_coord, y_coord = map(float, cleanup["coords"].split(","))

        total_trash_per_month_year[month_year] += weight
        total_volunteers_per_month_year[month_year] += group_size
        total_time_per_month_year[month_year] += combined_hours

        if current_month != month:
            current_month = month
            month_years.append(month_year)
            visited_locations = []

        # If cleanups are near each other within the same month, consider them in one location 
        nearby = False
        for x, y in visited_locations:
            if x > x_coord - 0.002 and x < x_coord + 0.002 and y > y_coord - 0.002 and y < y_coord + 0.002:
                nearby = True
        if not nearby:
            total_sites_per_month_year[month_year] += 1
            visited_locations.append((x_coord, y_coord))

    table_rows_html = ''
    for month_year in month_years:
        if total_trash_per_month_year[month_year] and total_volunteers_per_month_year[month_year] and total_sites_per_month_year[month_year]:
            table_rows_html += f'''
            <tr>
                <td class="cell no-bold">{month_year}</td>
                <td class="cell">{total_sites_per_month_year[month_year]}</td>
                <td class="cell">{total_volunteers_per_month_year[month_year]}</td>
                <td class="cell">{round(total_trash_per_month_year[month_year], 1)}</td>
            </tr>
            '''
        else:
            table_rows_html += f'''
            <tr>
                <td class="cell no-bold">{month_year}</td>
                <td class="cell"></td>
                <td class="cell"></td>
                <td class="cell"></td>
            </tr>
            '''

    return render_template('stats-embed.html', table = Markup(table_rows_html))

@app.route('/report', methods=['GET', 'POST'])
def report():
    """
    Deprecated. Adds a report to the reports map.  
    """
    if request.method == 'POST':
        wsheet = GOOGLE_SHEET.worksheet('Reports')
        report_data = wsheet.get_all_values()
        date_now = datetime.now(tz=pytz.utc)
        date_now = date_now.astimezone(timezone('America/Los_Angeles'))
        try: 
            report_data.append([request.form['location'], request.form['trash'], request.form['comment'], 'Not resolved', date_now.strftime('%m/%d/%Y'), '#DB4437'])
        except:
            report_data.append([request.form['x-location'] + ', ' + request.form['y-location'], request.form['trash'], request.form['comment'], 'Not resolved', date_now.strftime('%m/%d/%Y'), '#DB4437'])
        wsheet.update('A1:G' + str(len(report_data)), report_data)
        cell = wsheet.range('G1:G1')
        cell[0].value = '=GEO_MAP(A1:F' + str(len(report_data)) + ', "reports", "Location")'
        wsheet.update_cells(cell, 'USER_ENTERED')
    if request.form['embed'] == 'true':
        return redirect(url_for('render_maps_embed'))
    return redirect(url_for('render_maps'))

@app.route('/resolve', methods=['GET', 'POST'])
def resolve():
    """
    Deprecated. Removes a report from the reports map.
    """
    if request.method == 'POST':
        wsheet = GOOGLE_SHEET.worksheet('Resolve Requests')
        data_resolve = wsheet.get_all_values()
        data_resolve.append([request.form['resolve-name'], request.form['resolve-location'], request.form['resolve-date'].replace('-', '/'), request.form['resolve-notes']])
        wsheet.update('A1:G' + str(len(data_resolve)), data_resolve)
    return redirect(url_for('render_maps'))

if __name__ == "__main__":
    app.run()
