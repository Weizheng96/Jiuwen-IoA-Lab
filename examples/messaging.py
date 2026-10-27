"""Send an account-addressed free message from an explicitly configured local account."""
import argparse,os
from pathlib import Path
from ioalab_sdk.messaging import MailboxClient

def main():
    p=argparse.ArgumentParser();p.add_argument('recipient');p.add_argument('text');args=p.parse_args()
    box=MailboxClient(os.environ['IOALAB_BASE_URL'],os.environ['IOALAB_USERNAME'],os.environ['IOALAB_PASSWORD'],Path(os.environ.get('IOALAB_HOME','data/example-client')),connect_identity=True)
    try:
        interfaces=box.interfaces(args.recipient)
        if not any(i['name']=='message.forward' for i in interfaces):raise ValueError('The recipient has no message.forward interface open to this account')
        result=box.send(args.recipient,'message.forward',{'text':args.text});print(result['id'],result['state'])
    finally:box.close()

if __name__=='__main__':main()
