from ursina import *
app = Ursina(development_mode=False)
Entity(model='cube', color=color.red, scale=2)
EditorCamera()
app.run()
