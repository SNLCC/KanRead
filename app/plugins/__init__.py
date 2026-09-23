"""Internal feature registry. Only source-controlled builtins can be registered."""
from dataclasses import dataclass, field
from fastapi import APIRouter

@dataclass
class Feature:
    id: str
    requires: tuple[str,...] = ()
    router: APIRouter = field(default_factory=APIRouter)

class Registry:
    def __init__(self, features):
        self.features={}
        for feature in features:
            if feature.id in self.features:raise ValueError('Duplicate feature: '+feature.id)
            self.features[feature.id]=feature
        self.mounted=False
    def __getitem__(self,key):return self.features[key].router
    def order(self):
        ordered=[];visiting=set();done=set()
        def visit(key):
            if key in done:return
            if key in visiting:raise ValueError('Feature dependency cycle: '+key)
            if key not in self.features:raise ValueError('Missing feature dependency: '+key)
            visiting.add(key)
            for dependency in self.features[key].requires:visit(dependency)
            visiting.remove(key);done.add(key);ordered.append(self.features[key])
        for key in self.features:visit(key)
        return ordered
    def mount(self,app):
        if self.mounted:raise RuntimeError('Features already mounted')
        routes={(method,route.path) for route in app.routes for method in getattr(route,'methods',())}
        for feature in self.order():
            for route in feature.router.routes:
                for method in route.methods:
                    key=(method,route.path)
                    if key in routes:raise ValueError('Duplicate route: '+str(key))
                    routes.add(key)
            app.include_router(feature.router,tags=[feature.id])
        self.mounted=True
    def describe(self):
        return [{'id':f.id,'requires':list(f.requires),'routes':len(f.router.routes)} for f in self.order()]

def builtins():
    return Registry([Feature('models'),Feature('library'),Feature('reader',('library',)),
        Feature('annotations',('reader',)),Feature('translation',('annotations',)),
        Feature('search',('models','library')),
        Feature('chat',('search','reader')),Feature('zotero',('library',)),
        Feature('speech',('chat',)),Feature('session')])

def install_builtin_features(registry, host):
    # Explicit imports are the allowlist; never import a user-supplied module name.
    from . import models_feature,library_feature,reader_feature,annotations_feature
    from . import translation_feature
    from . import search_feature,chat_feature,zotero_feature,speech_feature,session_feature
    installers={'models':models_feature.install,'library':library_feature.install,
        'reader':reader_feature.install,'annotations':annotations_feature.install,
        'translation':translation_feature.install,
        'search':search_feature.install,'chat':chat_feature.install,
        'zotero':zotero_feature.install,'speech':speech_feature.install,
        'session':session_feature.install}
    for feature in registry.order():
        exports=installers[feature.id](feature.router,host)
        for name,service in (exports or {}).items():setattr(host,name,service)
