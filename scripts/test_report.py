"""Run all repository tests and preserve each runner outcome and exit status."""
import json
from pathlib import Path
import sys
import unittest
import argparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
class EvidenceResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.outcomes=[]
    def addSuccess(self,test):
        super().addSuccess(test);self.outcomes.append({'test':test.id(),'outcome':'passed'})
    def addFailure(self,test,err):
        super().addFailure(test,err);self.outcomes.append({'test':test.id(),'outcome':'failed'})
    def addError(self,test,err):
        super().addError(test,err);self.outcomes.append({'test':test.id(),'outcome':'error'})
    def addSkip(self,test,reason):
        super().addSkip(test,reason);self.outcomes.append({'test':test.id(),'outcome':'skipped','reason':reason})

parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,default=ROOT/'examples'/'validation-report.json')
args=parser.parse_args()
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
result=unittest.TextTestRunner(verbosity=2,resultclass=EvidenceResult).run(suite)
report={'tests_run':result.testsRun,'passed':sum(x['outcome']=='passed' for x in result.outcomes),'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'successful':result.wasSuccessful(),'outcomes':result.outcomes}
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if result.wasSuccessful() else 1)
