import argparse
import functools
import os
import time
import traceback
from datetime import timedelta, datetime

try:
   import cPickle as pickle
except:
   import pickle
import requests
from watchdog.observers import Observer

MAILGUN_DOMAIN_NAME = '<MAILGUN_DOMAIN_NAME>'
MAILGUN_API_KEY = '<MAILGUN_API_KEY>'
MAILGUN_DEFAULT_FROM = 'alert@cinnamonhills.org'

print = functools.partial(print, flush=True)
DEBUG = False

pickle_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pickle.db')


def clean_persist():
    with open(pickle_file, 'wb') as f_out:
        pickle.dump({}, f_out)


def set_persist(name, value, workspace=None):
    old_data = {}
    try:
        with open(pickle_file, 'rb') as f_in:
            old_data = pickle.load(f_in) or {}
    except Exception:
        pass
    if workspace:
        old_data.setdefault(workspace, {})[name] = value
    else:
        old_data[name] = value
    with open(pickle_file, 'wb') as f_out:
        pickle.dump(old_data, f_out)


def get_persist(name, default=None, workspace=None):
    data = {}
    try:
        with open(pickle_file, 'rb') as f_in:
            data = pickle.load(f_in) or {}
    except Exception:
        pass
    return (data.get(workspace) or {}).get(name, default) if workspace else data.get(name, default)


def send_mail(to, subject, text, html=None, from_email=None):
    assert to, 'to address cannot be blank!'

    if isinstance(to, str):
        to = [to]
    from_email = from_email or MAILGUN_DEFAULT_FROM
    print('!!! Sending Email to {} '.format(to))
    if DEBUG:
        print('From: {}\nTo: {}\nSubject: {}\nMessage:{}'.format(from_email, to, subject, text or html))
        return

    url = 'https://api.mailgun.net/v3/{}/messages'.format(MAILGUN_DOMAIN_NAME)
    auth = ('api', MAILGUN_API_KEY)
    data = {
        'from': from_email,
        'to': ','.join(to),
        'subject': subject,
        'text': text,
        'html': html,
    }

    response = requests.post(url, auth=auth, data=data)
    response.raise_for_status()


class NotifyIfIdleEventHandler(object):

    ALERT_SUBJECT = 'Un-changed Directory Warning'
    ALERT_MESSAGE = '<font color="red"><b>Warning!!! </b></font><p>The [{path}] path was unchanged for about ' \
                    '{idle_hours} hours!</p>'

    def __init__(self, path, idle_time_threshold=timedelta(hours=12), retry_alert_interval=None, event_types=None,
                 alert_type='email', email_to=None):
        self.path = path
        self.idle_time_threshold = idle_time_threshold
        self.retry_alert_interval = retry_alert_interval or idle_time_threshold
        self.event_types = event_types
        self.alert_type = alert_type
        self.email_to = email_to

    def check_send_alert(self):
        last_alert = get_persist('last_alert', workspace=self.path)
        if last_alert and (datetime.utcnow() - last_alert < self.retry_alert_interval):
            return False
        return True

    def send_alert(self):
        message = self.ALERT_MESSAGE.format(path=self.path, idle_hours=self.idle_time_threshold)
        try:
            if self.alert_type == 'email':
                send_mail(self.email_to, self.ALERT_SUBJECT, message, html=message)
            elif self.alert_type == 'sms':
                print('Send SMS not implemented yet')
            else:
                raise NotImplementedError('Not supported {} alert type'.format(self.alert_type))
        except Exception:
            traceback.print_exc()

        set_persist('last_alert', datetime.utcnow(), workspace=self.path)

    def check_idle(self):
        last_modified = get_persist('last_modified', workspace=self.path)
        if not last_modified:
            last_modified = datetime.utcnow()
            set_persist('last_modified', last_modified, workspace=self.path)
        if datetime.utcnow() - last_modified > self.idle_time_threshold:
            return True
        return False

    def dispatch(self, event):
        if (self.event_types is None) or event.event_type in self.event_types:
            what = 'directory' if event.is_directory else 'file'
            print('+++ New change Detected in "{}": [{} {}]'.format(self.path, event.event_type, what))
            set_persist('last_modified', datetime.utcnow(), workspace=self.path)

def main():
    global MAILGUN_DOMAIN_NAME, MAILGUN_API_KEY, MAILGUN_DEFAULT_FROM, DEBUG
    parser = argparse.ArgumentParser(description='Directory watcher.')
    parser.add_argument('paths', type=str, nargs='+', help='directory path')
    parser.add_argument('--alert-type', help='alert type', choices=['email', 'sms'], default='email')
    parser.add_argument('--email-to', help='email to address separated by comma for multiple')
    parser.add_argument('--idle-time-threshold', type=int, help='idle time threshold in minutes', default=12*60)
    parser.add_argument('--retry-alert-interval', type=int, help='retry alert interval in minutes')
    parser.add_argument('--mailgun-domain', help='mailgun domain name')
    parser.add_argument('--mailgun-api-key', help='mailgun api key')
    parser.add_argument('--debug', action='store_true', help='debugging mode', default=False)
    parser.add_argument('--clean', action='store_true', help='clean persistant data and start from scratch',
                        default=False)
    args = parser.parse_args()
    DEBUG = args.debug
    if args.clean:
        print('+++ Cleaning old data and starting from scratch...')
        clean_persist()
    paths = args.paths or '.'
    alert_type = args.alert_type
    if alert_type == 'email':
        if not args.email_to:
            parser.error("--email-to is required!")
        if not args.mailgun_domain:
            parser.error("--mailgun-domain is required!")
        if not args.mailgun_api_key:
            parser.error("--mailgun-api-key is required!")

    email_to = args.email_to.split(',') if args.email_to else None
    idle_time_threshold = timedelta(minutes=args.idle_time_threshold)
    retry_alert_interval = timedelta(minutes=args.retry_alert_interval) if args.retry_alert_interval else None
    print('Watching {} ...'.format(paths))
    if args.mailgun_domain:
        MAILGUN_DOMAIN_NAME = args.mailgun_domain
    if args.mailgun_api_key:
        MAILGUN_API_KEY = args.mailgun_api_key

    event_handlers = []
    observer = Observer()
    for path in paths:
        if not os.path.exists(path):
            print('Error!!! Invalid path: [{}]'.format(path))
            return
        event_handler = NotifyIfIdleEventHandler(
            path=path, idle_time_threshold=idle_time_threshold, alert_type=alert_type,
            retry_alert_interval=retry_alert_interval, email_to=email_to)
        observer.schedule(event_handler, path, recursive=True)
        event_handlers.append(event_handler)
    observer.start()
    try:
        while True:
            time.sleep(1)
            for event_handler in event_handlers:
                if event_handler.check_idle() and event_handler.check_send_alert():
                    print('!!! Long time idle detected in directory "{}" !!!'.format(event_handler.path))
                    try:
                        event_handler.send_alert()
                    except Exception:
                        print('cannot send alert!')
                        traceback.print_exc()

    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
