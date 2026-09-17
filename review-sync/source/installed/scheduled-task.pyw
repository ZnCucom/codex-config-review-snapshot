import runpy
import sys

sys.argv = ['C:\\Users\\chest\\.codex\\review-sync\\review_sync_scheduled.py', '--data-root', 'C:\\Users\\chest\\AppData\\Local\\CodexReviewSync', '--codex-home', 'C:\\Users\\chest\\.codex', '--git', 'C:\\Users\\chest\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\native\\git\\cmd\\git.exe', '--gh', 'C:\\Program Files\\GitHub CLI\\gh.exe', '--installation-id', '1667bf56a58c47948b47f163b9ccd943']
runpy.run_path(sys.argv[0], run_name='__main__')
