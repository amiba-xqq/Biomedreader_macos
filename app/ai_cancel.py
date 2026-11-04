"""Task-scoped cancellation. Never kill a Python worker or hold up the GUI.

Blocking transports run in a daemon worker; cancellation releases the Qt pool
worker promptly and discards late results. Transport aborts are best effort:
an upstream service may still finish/bill a request it already received.
"""
import queue
import threading
from contextlib import contextmanager

CANCEL_MESSAGE='已取消本地AI任务，后续调用已停止。已发送的云端请求仍可能完成或计费。'
class AICancelled(InterruptedError):pass
_local=threading.local()

class Cancellation:
    def __init__(self,requested=None):
        self.event=threading.Event();self.requested=requested;self.lock=threading.Lock();self.callbacks={}
    def cancelled(self):
        if self.event.is_set():return True
        if self.requested and self.requested():self.cancel();return True
        return False
    def cancel(self):
        with self.lock:
            if self.event.is_set():return
            self.event.set();callbacks=list(self.callbacks.values());self.callbacks.clear()
        for callback in callbacks:threading.Thread(target=self._abort,args=(callback,),daemon=True).start()
    @staticmethod
    def _abort(callback):
        try:callback()
        except Exception:pass
    def register(self,callback):
        key=object()
        with self.lock:
            cancelled=self.event.is_set()
            if not cancelled:self.callbacks[key]=callback
        if cancelled:threading.Thread(target=self._abort,args=(callback,),daemon=True).start()
        def remove():
            with self.lock:self.callbacks.pop(key,None)
        return remove

def current():return getattr(_local,'cancellation',None)

@contextmanager
def scope(token):
    previous=current();_local.cancellation=token
    try:yield
    finally:_local.cancellation=previous

def check_cancelled(requested=None):
    token=current()
    if (token and token.cancelled()) or (requested and requested()):raise AICancelled(CANCEL_MESSAGE)

def register_abort(callback):
    token=current()
    return token.register(callback) if token else lambda:None

def interruptible(work,token):
    """Return on cancellation even while a provider's blocking read is pending."""
    result=queue.Queue(maxsize=1)
    def execute():
        with scope(token):
            try:check_cancelled();value=work();check_cancelled();result.put((True,value))
            except BaseException as error:result.put((False,error))
    with scope(token):
        check_cancelled()
        threading.Thread(target=execute,name='BiomedAI',daemon=True).start()
        while True:
            check_cancelled()
            try:ok,value=result.get(timeout=.05)
            except queue.Empty:continue
            check_cancelled()
            if not ok:raise value
            return value
