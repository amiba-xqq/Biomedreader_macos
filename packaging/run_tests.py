"""Report the first native GUI failure immediately, without orphaned dialogs."""
import faulthandler,sys,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'app'),str(root/'tests')]
faulthandler.enable();faulthandler.dump_traceback_later(120,repeat=True)
class ImmediateResult(unittest.TextTestResult):
    def addFailure(self,test,error):
        super().addFailure(test,error);self.stream.writeln(self._exc_info_to_string(error,test))
    def addError(self,test,error):
        super().addError(test,error);self.stream.writeln(self._exc_info_to_string(error,test))
suite=unittest.defaultTestLoader.discover(str(root/'tests'))
result=unittest.TextTestRunner(verbosity=2,failfast=True,resultclass=ImmediateResult).run(suite)
faulthandler.cancel_dump_traceback_later()
raise SystemExit(0 if result.wasSuccessful() else 1)
