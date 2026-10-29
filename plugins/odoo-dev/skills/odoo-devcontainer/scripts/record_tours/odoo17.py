"""record-tours patches for Odoo 17.0 and 18.0.

Both series use the ``TourPointer`` component and the ``odoo.startTour`` /
``odoo.isTourReady`` page functions, and both keep the screencast frames in
``ChromeBrowser.screencast_frames``. Only 17.0 was run against a real
database. 18.0 is covered by the same code on the strength of the source
comparison in issue 1049.
"""
import json

from record_tours import common as shared

# Module path of the TourPointer in the page's module loader. If a series moves
# it, the pointer patch does nothing and the caption bar still works.
POINTER_MODULE = '@web_tour/tour_pointer/tour_pointer'


def patch_start_tour(odoo_common, settings):
    def start_tour(self, url_path, tour_name, step_delay=None, **kwargs):
        options = {
            'stepDelay': step_delay if step_delay else settings.step_delay,
            'keepWatchBrowser': kwargs.get('watch', False),
            'startUrl': url_path,
            'showPointerDuration': settings.pointer_duration,
        }
        code = shared.pointer_code(POINTER_MODULE, tour_name, json.dumps(options))
        kwargs.pop('code', None)
        kwargs.pop('ready', None)
        kwargs.setdefault('timeout', settings.timeout)
        type(self)._record_tour_prefix = tour_name
        try:
            return self.browser_js(
                url_path=url_path, code=code, ready="odoo.isTourReady('%s')" % tour_name, **kwargs)
        finally:
            type(self)._record_tour_prefix = None

    odoo_common.HttpCase.start_tour = start_tour


def patch_browser_stop(odoo_common, settings):
    original_stop = odoo_common.ChromeBrowser.stop

    def stop(self):
        prefix = getattr(self.test_class, '_record_tour_prefix', None)
        if prefix and self.screencasts_dir and self.screencast_frames and not self.had_failure:
            shared.save_recording(self, prefix, settings)
        return original_stop(self)

    odoo_common.ChromeBrowser.stop = stop


def apply(odoo_common, settings):
    patch_start_tour(odoo_common, settings)
    patch_browser_stop(odoo_common, settings)
