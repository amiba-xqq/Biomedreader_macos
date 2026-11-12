"""Report the first native GUI failure immediately, without orphaned dialogs."""
import faulthandler,sys,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'app'),str(root/'tests')]
# The workflow bounds the suite duration. A recurring traceback timer can
# interrupt Qt teardown while the native crash handler is active.
faulthandler.enable()
class ImmediateResult(unittest.TextTestResult):
    def addFailure(self,test,error):
        super().addFailure(test,error);self.stream.writeln(self._exc_info_to_string(error,test))
    def addError(self,test,error):
        super().addError(test,error);self.stream.writeln(self._exc_info_to_string(error,test))
suite=unittest.defaultTestLoader.discover(str(root/'tests'))
result=unittest.TextTestRunner(verbosity=2,failfast=True,resultclass=ImmediateResult).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
