"""Linux/systemd watcher with persistent backoff."""
import argparse
import logging
import time
import monitor

def run_once():
    monitor.cycle()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    if args.once: run_once()
    else:
        while True:
            try: run_once()
            except Exception as error: logging.error('Check failed: %s',type(error).__name__)
            time.sleep(max(15,int(monitor.core.load_settings().get('poll_interval',120))))

