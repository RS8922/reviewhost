import schedule, time
from outreach import daily_run

print('ReviewHost Scheduler gestart - draait elke 2 uur')
daily_run()
schedule.every(2).hours.do(daily_run)
while True:
    schedule.run_pending()
    time.sleep(30)
