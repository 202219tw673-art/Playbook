from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = '''
    callback: mail_summary
    type: notification
    short_description: Emails a line-by-line task recap after the playbook finishes
    description:
        - Records the result (ok/changed/failed/skipped/unreachable) of every task,
          for every host, and emails a plain-text recap when the play finishes.
    requirements:
        - none (uses stdlib smtplib)
    options:
        smtp_host:
            description: SMTP server hostname
            env:
                - name: MAIL_SUMMARY_SMTP_HOST
            default: localhost
        smtp_port:
            description: SMTP server port
            env:
                - name: MAIL_SUMMARY_SMTP_PORT
            default: 25
        mail_from:
            description: From address
            env:
                - name: MAIL_SUMMARY_FROM
            default: awx-alerts@hdfc.local
        mail_to:
            description: Comma separated list of recipients
            env:
                - name: MAIL_SUMMARY_TO
            required: true
        only_on_failure:
            description: If true, only send the email when the run had at least one failure
            env:
                - name: MAIL_SUMMARY_ONLY_ON_FAILURE
            default: false
            type: bool
'''

import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    """
    Collects per-task, per-host results as the playbook runs, then emails
    a formatted line-by-line recap (task name -> status) once the play
    finishes, or once for every play if run_once_per_play is False.
    """

    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'notification'
    CALLBACK_NAME = 'mail_summary'
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self):
        super(CallbackModule, self).__init__()
        self.results = []          # list of dicts: task, host, status
        self.playbook_name = None
        self.start_time = None

    def v2_playbook_on_start(self, playbook):
        self.playbook_name = os.path.basename(playbook._file_name)
        self.start_time = datetime.now()

    def _record(self, result, status):
        task_name = result._task.get_name()
        host_name = result._host.get_name()
        self.results.append({'task': task_name, 'host': host_name, 'status': status})

    def v2_runner_on_ok(self, result):
        status = 'changed' if result._result.get('changed', False) else 'ok'
        self._record(result, status)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._record(result, 'failed (ignored)' if ignore_errors else 'failed')

    def v2_runner_on_skipped(self, result):
        self._record(result, 'skipped')

    def v2_runner_on_unreachable(self, result):
        self._record(result, 'unreachable')

    def v2_playbook_on_stats(self, stats):
        smtp_host = self.get_option('smtp_host') if self.get_option('smtp_host') else os.environ.get('MAIL_SUMMARY_SMTP_HOST', 'localhost')
        smtp_port = int(os.environ.get('MAIL_SUMMARY_SMTP_PORT', 25))
        mail_from = os.environ.get('MAIL_SUMMARY_FROM', 'awx-alerts@hdfc.local')
        mail_to = os.environ.get('MAIL_SUMMARY_TO')
        only_on_failure = os.environ.get('MAIL_SUMMARY_ONLY_ON_FAILURE', 'false').lower() == 'true'

        if not mail_to:
            # No recipient configured, nothing to send
            return

        hosts = sorted(stats.processed.keys())
        had_failure = any(stats.summarize(h).get('failures', 0) or stats.summarize(h).get('unreachable', 0) for h in hosts)

        if only_on_failure and not had_failure:
            return

        lines = []
        lines.append("Playbook: {0}".format(self.playbook_name))
        lines.append("Started:  {0}".format(self.start_time))
        lines.append("Finished: {0}".format(datetime.now()))
        lines.append("")
        lines.append("Task-by-task results:")
        lines.append("-" * 60)

        width = max((len(r['task']) for r in self.results), default=20) + 2
        for r in self.results:
            line = "{task:<{w}} [{host}] {status}".format(
                task=r['task'], w=width, host=r['host'], status=r['status'].upper()
            )
            lines.append(line)

        lines.append("-" * 60)
        lines.append("")
        lines.append("Summary per host:")
        for h in hosts:
            s = stats.summarize(h)
            lines.append(
                "{0}: ok={1} changed={2} unreachable={3} failed={4} skipped={5}".format(
                    h, s['ok'], s['changed'], s['unreachable'], s['failures'], s['skipped']
                )
            )

        body = "\n".join(lines)
        subject_status = "FAILED" if had_failure else "SUCCESS"
        msg = MIMEText(body)
        msg['Subject'] = "[{0}] Playbook {1} - {2}".format(subject_status, self.playbook_name, self.start_time.strftime('%Y-%m-%d %H:%M'))
        msg['From'] = mail_from
        msg['To'] = mail_to

        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.sendmail(mail_from, mail_to.split(','), msg.as_string())
        except Exception as e:
            self._display.warning("mail_summary callback: failed to send email: {0}".format(e))
