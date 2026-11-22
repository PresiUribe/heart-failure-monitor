"""Evaluación de la recuperación: Recall@1, Recall@3 y MRR de BM25, denso e híbrido.

Las preguntas se evalúan solo contra guías y normas (documento esperado conocido).
El ranking se lleva a nivel de documento: si dos fragmentos del mismo documento salen
seguidos, cuenta la primera aparición.
"""

# (pregunta, documento esperado). Unas usan los términos técnicos de la guía (favorecen a BM25) y
# otras están en lenguaje de paciente o parafraseadas (favorecen a la búsqueda densa).
EVAL = [
    ("¿qué tan bajo tiene que estar el bombeo del corazón para hablar de falla con función disminuida?", "G01"),
    ("paciente que se agita apenas camina dentro de la casa, ¿en qué categoría de gravedad queda?", "G01"),
    ("¿qué examen de sangre ayuda a confirmar la falla cardiaca?", "G01"),
    ("¿qué pastillas le bajan el riesgo de morir a alguien con el corazón débil?", "G02"),
    ("¿el medicamento para orinar alarga la vida?", "G02"),
    ("¿cuándo está prohibido arrancar con espironolactona?", "G02"),
    ("la báscula marca dos kilos más desde el lunes", "G03"),
    ("me toca dormir sentado porque me ahogo acostado", "G03"),
    ("¿puedo tomar ibuprofeno para el dolor de rodilla?", "G03"),
    ("¿cuánto líquido debe tomar al día alguien con falla cardiaca grave?", "G03"),
    ("¿por qué los riñones empeoran cuando el corazón falla?", "G04"),
    ("le subió el potasio con los medicamentos del corazón, ¿qué se revisa?", "G04"),
    ("¿qué dos variables predicen la supervivencia según Chicco y Jurman?", "G04"),
    ("¿desde qué valor de sodio se habla de hiponatremia?", "G05"),
    ("¿qué pasa si corrijo la sal en la sangre demasiado rápido?", "G05"),
    ("¿cómo se mide en la ecografía cuánta sangre expulsa el ventrículo?", "G06"),
    ("¿qué vistas se usan en el método de Simpson biplano?", "G06"),
    ("¿qué contiene el dataset CAMUS?", "G06"),
    ("le dieron salida del hospital ayer, ¿cuándo debe volver a control?", "G07"),
    ("¿sirve vigilar a distancia a pacientes que viven lejos en el campo?", "G07"),
    ("¿cuándo mandarlo al cardiólogo para ponerle un desfibrilador?", "G07"),
    ("¿puedo mandar la cédula del paciente a ChatGPT?", "N01"),
    ("¿qué ley obliga a que la historia clínica se pueda compartir entre hospitales?", "N01"),
    ("¿por qué no se borran los registros de la historia clínica?", "N01"),
    ("¿con qué probabilidad el sistema dispara la alerta roja?", "N02"),
    ("¿el porcentaje que calcula la IA es un diagnóstico?", "N02"),
    ("¿en qué pacientes falla más el modelo de riesgo?", "N02"),
]

# Preguntas fuera del dominio: sirven para calibrar los umbrales de abstención.
FUERA_DE_DOMINIO = [
    "¿quién ganó el mundial de fútbol de 2014?",
    "receta de arepa de choclo",
    "¿cuál es la capital de Australia?",
    "¿qué celular me recomiendas comprar?",
    "¿a qué hora abre el banco el sábado?",
]


def a_documentos(chunk_ids: list[str], doc_de: dict[str, str]) -> list[str]:
    vistos, salida = set(), []
    for cid in chunk_ids:
        d = doc_de.get(cid)
        if d and d not in vistos:
            vistos.add(d)
            salida.append(d)
    return salida


def metricas(rankings_doc: list[list[str]], esperados: list[str]) -> dict:
    n = len(esperados)
    r1 = sum(1 for r, e in zip(rankings_doc, esperados) if e in r[:1]) / n
    r3 = sum(1 for r, e in zip(rankings_doc, esperados) if e in r[:3]) / n
    mrr = sum((1.0 / (r.index(e) + 1)) if e in r else 0.0 for r, e in zip(rankings_doc, esperados)) / n
    return {"recall@1": round(r1, 3), "recall@3": round(r3, 3), "mrr": round(mrr, 3)}
