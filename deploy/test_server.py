"""Explicit destructive maintenance for an authorized disposable test deployment."""
from pathlib import Path
import shutil
import time

from . import activation, units
from .agently import authorization_paths
from .files import path_without_links


def remove(path):
    path=Path(path)
    path_without_links(path.parent)
    if path.is_symlink():path.unlink()
    elif path.is_dir():shutil.rmtree(path)
    elif path.exists():path.unlink()


def clear_state(layout,metadata,files,*,released):
    layout.inspect()
    if not released():raise RuntimeError('Test reset requires released original writers')
    retained=tuple(path_without_links(path) for path in authorization_paths(layout))
    for path in retained:
        for ancestor in (path,*path.parents):
            if ancestor==layout.state/'agents':break
            if ancestor.exists() and not ancestor.is_dir():raise RuntimeError('Restore the original external authorization directories')
    def clear(path):
        if path in retained:return
        if any(keep.is_relative_to(path) for keep in retained):
            for child in path.iterdir():clear(child)
        else:remove(path)
    for role in ('core','agents'):
        home=path_without_links(layout.state/role)
        if not home.is_dir():raise RuntimeError('Restore the original role state directory')
        for child in home.iterdir():
            clear(child)
        cache=path_without_links(layout.state/'cache'/role)
        if cache.exists():
            for child in cache.iterdir():remove(child)
    for folder in (layout.config/'agents',layout.private/'admin-assistant'):
        folder=path_without_links(folder)
        if folder.exists():
            for child in folder.iterdir():remove(child)
    for key in list(files):
        path=Path(key)
        if path.is_relative_to(layout.config/'agents') or path.is_relative_to(layout.private/'admin-assistant'):
            del files[key]
    for key in list(metadata.get('paths',{})):
        path=Path(key)
        if any(path!=layout.state/role and path.is_relative_to(layout.state/role) for role in ('core','agents')):
            if not any(path==keep or path.is_relative_to(keep) or keep.is_relative_to(path) for keep in retained):del metadata['paths'][key]
    for field in ('initialized','officialPrepared','officialAuthorized','administratorPrepared'):
        metadata[field]=False
    for name in ('lifecycle.json','replace-admin-assistant.json','retired-isc-assistant'):
        remove(layout.private/name)


def prune(layout,*,released,operation=None):
    layout.inspect()
    if not released():raise RuntimeError('Pruning requires released original writers')
    current=activation.current(layout)
    if current is None:raise RuntimeError('Verify the current complete environment set before pruning')
    groups={Path(entry['environment']).parent:Path(entry['environment']) for entry in current['units'].values()}
    for child in layout.environments.iterdir():
        if child.name=='current':continue
        if child.name=='sets':
            for item in child.iterdir():
                if item.name!=current['setId']:remove(item)
        elif child in groups:
            for item in child.iterdir():
                if item.is_dir() and item!=groups[child]:remove(item)
        else:remove(child)
    for child in (layout.root/'releases').iterdir():
        if str(child)!=current['release']:remove(child)
    remove(layout.state/'backups')
    jobs=layout.private/'jobs'
    if operation and jobs.exists():
        for child in jobs.iterdir():
            if child.name!=operation:remove(child)
    assert activation.current(layout)==current
    return {'setId':current['setId'],'releaseId':current['releaseId'],'singleVersion':True}


def role_processes(metadata):
    """The reset worker is root; any remaining service-account process blocks deletion."""
    uids={entry['uid'] for entry in metadata.get('accounts',{}).values()}
    if not uids or 0 in uids:raise RuntimeError('Verify the dedicated role accounts before test reset')
    for item in Path('/proc').iterdir():
        if not item.name.isdecimal():continue
        try:
            if item.stat().st_uid in uids:return True
        except FileNotFoundError:pass
    return False


class Reset:
    def __init__(self,installer):
        self.installer=installer;self.layout=installer.layout
        self.record=installer.progress['testReset']

    def saved(self,**values):
        self.record.update(values);self.installer.saved()

    def released(self):
        i=self.installer
        return all(i.manager.released(name,state) for name,state in self.record['before'].items()) and not role_processes(i.metadata)

    def run(self):
        from .lifecycle import runtime_leases
        i=self.installer
        if not self.record:
            previous=activation.current(self.layout)
            if previous is None or not i.metadata.get('initialized'):
                raise RuntimeError('Test reset requires a completed existing deployment')
            self.saved(phase='quiescing',previous=previous,
                before=i.manager.freeze([row['unit'] for row in i.plans.values()]))
        if self.record['phase']=='quiescing':
            for group in units.STOP_GROUPS:
                names=[i.plans[key]['unit'] for key in group if key in i.plans]
                for name in names:i.manager.request_stop(name,self.record['before'][name])
                while not all(i.manager.released(name,self.record['before'][name]) for name in names):time.sleep(.2)
            with runtime_leases(self.layout,i.plans):
                if not self.released():raise RuntimeError('A service-account process or original writer remains')
                self.saved(phase='clearing')
                self.clear()
        elif self.record['phase']=='clearing':
            # The namespace retirement was persisted while holding the original
            # locks. A crash may leave some old lock files already removed.
            if not self.released():raise RuntimeError('A writer reappeared during the original test reset')
            self.clear()
        if self.record['phase']=='pruning':
            current=activation.current(self.layout)
            if current!=i.progress['candidate']:
                activation.activate(self.layout,i.progress['candidate'],self.record['previous']['setId'])
            prune(self.layout,released=self.released,operation=i.job.operation)
            self.saved(phase='initializing')
        if self.record['phase']=='initializing':
            i.config.base();i.permissions();i.bootstrap()
            self.saved(phase='completed')
        if self.record['phase']!='completed':raise RuntimeError('Restore the original test reset phase')

    def clear(self):
        i=self.installer
        clear_state(self.layout,i.metadata,i.config.record['files'],released=self.released)
        i.save_metadata();i.config.persist();self.saved(phase='pruning')
