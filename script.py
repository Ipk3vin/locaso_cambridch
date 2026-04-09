
import requests
import re
import json
import html
import time
from lxml import etree
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException
import subprocess
import os

# =========================================
# 🔹 CARGAR DATA.JS DESDE EL NAVEGADOR
# =========================================

def get_ajax_data_directly(driver):
    """Busca la variable global ajaxData en la memoria de la ventana o en sus iframes."""
    try:
        data = driver.execute_script("return typeof ajaxData !== 'undefined' ? JSON.stringify(ajaxData) : null;")
        if data:
            return json.loads(data), None
    except:
        pass
        
    try:
        # Intentamos obtener los iframes. Si falla aquí, es que la página cambió.
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for i in range(len(iframes)):
            try:
                # Volvemos a pedir los iframes para asegurar que no sean stale
                re_iframes = driver.find_elements(By.TAG_NAME, "iframe")
                if i >= len(re_iframes): break
                
                target_iframe = re_iframes[i]
                driver.switch_to.frame(target_iframe)
                data = driver.execute_script("return typeof ajaxData !== 'undefined' ? JSON.stringify(ajaxData) : null;")
                
                if data:
                    res = json.loads(data)
                    driver.switch_to.default_content()
                    return res, target_iframe
                
                driver.switch_to.default_content()
            except Exception:
                try: driver.switch_to.default_content()
                except: pass
    except Exception:
        pass
            
    return None, None


# =========================================
# 🔹 FUNCIONES ORIGINALES DE PARSEO (LIMPIAR XML Y EXTRAER)
# =========================================

def clean_xml(xml_string):
    xml_string = html.unescape(xml_string)
    xml_string = html.unescape(xml_string)
    xml_string = re.sub(r'<!\[CDATA\[.*?\]\]>', '', xml_string, flags=re.DOTALL)
    xml_string = re.sub(r'\s+data-author="\{.*?\}"', '', xml_string)
    xml_string = re.sub(r'\s+data-author=(?:"[^"]*"|\'[^\']*\')', '', xml_string)
    xml_string = re.sub(r'<assets>.*?</assets>', '', xml_string, flags=re.DOTALL)
    xml_string = re.sub(r'=\s*""+"', '=""', xml_string)
    xml_string = re.sub(r'label="<p>(.*?)</p>"', r'label="\1"', xml_string)
    xml_string = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#)', '&amp;', xml_string)
    return xml_string

def get_all_text(node):
    parts = []
    if node.text:
        parts.append(node.text.strip())
    for child in node:
        parts.append(get_all_text(child))
        if child.tail:
            parts.append(child.tail.strip())
    return " ".join(p for p in parts if p)

def parse_question(xml_string, nombre, tipo):
    xml_string = clean_xml(xml_string)
    try:
        root = etree.fromstring(xml_string.encode('utf-8'))
    except Exception as e:
        return None

    ns_url = root.nsmap.get(None, "http://www.imsglobal.org/xsd/imsqti_v2p1")
    ns = {"qti": ns_url}

    instruccion = ""
    for p in root.findall(".//qti:div[@id='rubric']//qti:p", ns):
        txt = get_all_text(p)
        if txt:
            instruccion = txt
            break

    inline_blocks = root.findall(".//qti:inlineChoiceInteraction", ns)
    if inline_blocks:
        preguntas_grupo = []
        # Buscamos cada interaccion donde sea que este (sin forzar <p>)
        for interaction in inline_blocks:
            resp_id = interaction.attrib.get("responseIdentifier", "")
            correct_id = None
            correct_decl = root.find(f".//qti:responseDeclaration[@identifier='{resp_id}']//qti:value", ns)
            if correct_decl is not None: correct_id = correct_decl.text
            
            opciones, correcta = [], None
            for choice in interaction.findall(".//qti:inlineChoice", ns):
                texto = get_all_text(choice).strip()
                ident = choice.attrib.get("identifier", "")
                opciones.append(texto)
                if ident == correct_id: correcta = texto
                
            if correcta:
                preguntas_grupo.append({"pregunta": instruccion, "opciones": opciones, "correctas": [correcta]})
        
        if len(preguntas_grupo) == 1:
            return {"archivo": nombre, "tipo": tipo, "pregunta": preguntas_grupo[0]["pregunta"], "opciones": preguntas_grupo[0]["opciones"], "correctas": preguntas_grupo[0]["correctas"]}
        elif len(preguntas_grupo) > 1:
            return {"archivo": nombre, "tipo": tipo, "pregunta": instruccion, "sub_preguntas": preguntas_grupo, "opciones": [], "correctas": []}

    choice_interactions = root.findall(".//qti:choiceInteraction", ns)
    if choice_interactions:
        textos = []
        for p in root.findall(".//qti:div[@id='contentblock']//qti:p", ns):
            if not p.findall(".//qti:choiceInteraction", ns):
                txt = get_all_text(p)
                if txt: textos.append(txt)
        pregunta_txt = instruccion + " " + " ".join(textos) if instruccion else " ".join(textos)
        opciones, mapa = [], {}
        for interaction in choice_interactions:
            for choice in interaction.findall("qti:simpleChoice", ns):
                texto = get_all_text(choice)
                ident = choice.attrib.get("identifier", "")
                opciones.append(texto)
                mapa[ident] = texto
        correctas = [mapa[val.text.strip()] for val in root.findall(".//qti:correctResponse//qti:value", ns) if (val.text or "").strip() in mapa]
        return {"archivo": nombre, "tipo": tipo, "pregunta": pregunta_txt.strip(), "opciones": opciones, "correctas": correctas}

    gap_interactions = root.findall(".//qti:gapMatchInteraction", ns)
    if gap_interactions:
        mapa_gap = {}
        for interaction in gap_interactions:
            for gap_text in interaction.findall("qti:gapText", ns):
                mapa_gap[gap_text.attrib.get("identifier", "")] = get_all_text(gap_text).strip()
        pares_correctos = []
        for val in root.findall(".//qti:correctResponse//qti:value", ns):
            par = (val.text or "").strip()
            if par and len(par.split()) == 2:
                pares_correctos.append(tuple(par.split()))
        pares_ordenados = sorted(pares_correctos, key=lambda x: x[1])
        palabras_orden = [mapa_gap.get(gtid, "") for gtid, _ in pares_ordenados]
        return {"archivo": nombre, "tipo": tipo, "pregunta": instruccion.strip(), "opciones": palabras_orden, "correctas": palabras_orden}

    text_entry_interactions = root.findall(".//qti:textEntryInteraction", ns)
    if text_entry_interactions:
        correctas_dict = {}
        for decl in root.findall(".//qti:responseDeclaration", ns):
            ident = decl.attrib.get("identifier", "")
            val_node = decl.find(".//qti:correctResponse//qti:value", ns)
            if val_node is not None: correctas_dict[ident] = (val_node.text or "").strip()
        correctas = [correctas_dict[interaction.attrib.get("responseIdentifier", "")] for interaction in text_entry_interactions if interaction.attrib.get("responseIdentifier", "") in correctas_dict]
        return {"archivo": nombre, "tipo": tipo, "pregunta": instruccion.strip(), "opciones": [], "correctas": correctas}

    opciones, mapa = [], {}
    for choice in root.xpath(".//*[local-name()='simpleChoice' or local-name()='simpleAssociableChoice']"):
        texto = get_all_text(choice)
        ident = choice.attrib.get("identifier", "")
        if texto: opciones.append(texto)
        mapa[ident] = texto
    correctas = []
    for val in root.findall(".//qti:correctResponse//qti:value", ns):
        cid = (val.text or "").strip()
        if not cid: continue
        if " " in cid:
            partes = [mapa.get(p, p).strip() for p in cid.split() if mapa.get(p)]
            if partes: correctas.append(" -> ".join(partes))
        elif cid in mapa:
            correctas.append(mapa[cid])
    return {"archivo": nombre, "tipo": tipo, "pregunta": instruccion.strip(), "opciones": opciones, "correctas": correctas}

def parse_learning_object(xml_string):
    xml_string = clean_xml(xml_string)
    try:
        root = etree.fromstring(xml_string.encode('utf-8'))
    except:
        return []
    screens = []
    for screen in root.findall(".//screen"):
        screens.append({
            "archivo": screen.findtext("name"), 
            "tipo": screen.findtext("activityTypeName"),
            "puntuacion": screen.findtext("maximumscore")
        })
    return screens

# =========================================
# 🔹 RESOLUCIÓN ULTRA-RÁPIDA (DETERMINISTA)
# =========================================

def resolver_pantalla_js(driver, frame_elemento, respuestas_planas):
    """
    Inyecta un código Javascript que lee la lista de respuestas planas y, 
    de manera automática, hace click en los botones en pantalla equivalentes, 
    o rellena los inputs que encuentre vacíos. ¡Tiempo de ejecución 0 milisegundos!
    """
    if not respuestas_planas:
        return False
        
    js_code = """
    let answers = arguments[0];
    let callback = arguments[1];
    
    function supremeClick(el) {
        if(!el) return;
        ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
            el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
        });
    }

    async function fillFast() {
        let doneCount = 0;
        
        // ESTRATEGIA 1: INPUTS DE TEXTO (TEXT ENTRY)
        let tInputs = Array.from(document.querySelectorAll('input[type="text"], input[type="number"], input:not([type="radio"]):not([type="checkbox"]):not([type="hidden"]), textarea, [contenteditable="true"]')).filter(e => e.offsetParent !== null);
        if (tInputs.length > 0 && tInputs.length >= answers.length) {
            for(let i=0; i<answers.length; i++) {
                let ansLow = answers[i];
                let inp = tInputs[i];
                inp.dispatchEvent(new Event('focus', {bubbles: true}));
                if(inp.tagName === 'INPUT' || inp.tagName === 'TEXTAREA') {
                    inp.value = ""; inp.value = ansLow;
                } else {
                    inp.innerText = ansLow;
                }
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                inp.dispatchEvent(new Event('blur', {bubbles: true}));
                doneCount++;
                await new Promise(r => setTimeout(r, 100));
            }
            callback(doneCount > 0); return;
        }
        
        // ESTRATEGIA 2: DROPDOWNS CUSTOMS (CAMBRIDGE ONE)
        let drops = Array.from(document.querySelectorAll('span, div, button, a, [role="button"], [role="combobox"], [aria-haspopup]')).filter(e => {
            let cls = (e.className || "").toLowerCase();
            let attr = (e.getAttribute('aria-haspopup') || "").toLowerCase();
            let role = (e.getAttribute('role') || "").toLowerCase();
            let isClickable = cls.includes('gap') || cls.includes('select') || cls.includes('dropdown') || attr === 'true' || attr === 'listbox' || role === 'combobox' || role === 'button';
            return isClickable && e.offsetParent !== null;
        });
        
        // Limpiamos anidados
        drops = drops.filter(d => !drops.some(parent => parent !== d && parent.contains(d)));
        
        if (drops.length > 0) {
            let limit = Math.min(drops.length, answers.length);
            let solvedAny = false;
            for(let i=0; i<limit; i++) {
                let drop = drops[i];
                let ansLow = answers[i].trim().toLowerCase();
                
                // Abrir el dropdown
                supremeClick(drop);
                drop.click(); 
                await new Promise(r => setTimeout(r, 600)); // Esperar a que abra
                
                // Buscar la opción por texto (robusto)
                let opts = Array.from(document.querySelectorAll('span, div, li, option, a, [role="option"]')).filter(e => {
                    if (e.offsetParent === null) return false;
                    let text = (e.innerText || "").trim().toLowerCase();
                    // Limpieza proactiva de &nbsp; y espacios raros
                    text = text.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
                    let cleanAns = ansLow.replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
                    
                    return text === cleanAns || (text.includes(cleanAns) && text.length < cleanAns.length + 5);
                });
                
                if (opts.length > 0) {
                    opts.sort((a,b) => (window.getComputedStyle(b).cursor==='pointer'?1:0) - (window.getComputedStyle(a).cursor==='pointer'?1:0));
                    let targetOpt = opts[0];
                    supremeClick(targetOpt);
                    targetOpt.click();
                    solvedAny = true;
                    doneCount++;
                } else {
                    // Fallback: Si no se encontró la opción, intentar click afuera y reintentar con el siguiente
                    supremeClick(document.body);
                }
                await new Promise(r => setTimeout(r, 500)); 
            }
            if(solvedAny) { callback(doneCount > 0); return; }
        }
        
        // ESTRATEGIA 2.5: WORD BANK / CLICK-TO-FILL (Gap Fill - Cambridge One)
        // Cambridge One usa click en la palabra → se llena el siguiente gap vacío automáticamente
        // Pero para asegurar, intentaremos: Click GAP -> Click PALABRA
        let wordBankContainers = Array.from(document.querySelectorAll(
            'li.gap_match_gap_text_view, li[class*="gap_match_gap_text"], ' +
            '[class*="gap_text_view"], .drag_element, [class*="drag_element"], .draggable, .om-textgap-element, [class*="word"] button'
        )).filter(e => e.offsetParent !== null);
        
        let gaps = Array.from(document.querySelectorAll(
            '.gap_match_gap_view, [class*="gap_view"], .gap-element, [class*="gap-container"], .gap'
        )).filter(e => e.offsetParent !== null && !e.className.includes('text_view'));

        if (wordBankContainers.length > 0) {
            let solvedAny = false;
            
            for(let i = 0; i < answers.length; i++) {
                let ansLow = answers[i].trim().toLowerCase();
                
                // 1. Opcional: Click en el gap de destino primero si existe
                if (gaps[i]) {
                    supremeClick(gaps[i]);
                    await new Promise(r => setTimeout(r, 400));
                }

                // 2. Encontrar el word bank item que contiene la respuesta
                // Buscamos de nuevo en cada iteración por si el DOM cambió
                let currentItems = Array.from(document.querySelectorAll(
                    'li, div, button, span, .drag_element, .om-textgap-element'
                )).filter(e => {
                    let text = (e.innerText || "").trim().toLowerCase().replace(/\s+/g, ' ');
                    let isVisible = e.offsetParent !== null;
                    let cls = (e.className || "");
                    let isCandidate = cls.includes('drag') || cls.includes('gap') || cls.includes('word') || cls.includes('option') || e.tagName === 'BUTTON';
                    return isVisible && isCandidate && (text === ansLow || text.includes(ansLow) && text.length < ansLow.length + 5);
                });

                let sourceItem = currentItems.find(item => {
                    let text = (item.innerText || "").trim().toLowerCase().replace(/\s+/g, ' ');
                    return text === ansLow || text.includes(ansLow);
                });
                
                if (!sourceItem) continue;
                
                // 3. Clickear la palabra
                let clickTarget = sourceItem.querySelector('button') 
                               || sourceItem.querySelector('[class*="content"]')
                               || sourceItem.querySelector('span')
                               || sourceItem;
                
                supremeClick(clickTarget);
                clickTarget.click();
                
                solvedAny = true;
                doneCount++;
                
                // 4. ESPERAR SECUENCIALMENTE (Muy importante para no traslapar eventos)
                await new Promise(r => setTimeout(r, 1200)); 
            }
            
            if(solvedAny) { callback(doneCount > 0); return; }
        }
        
        // ESTRATEGIA 3: NATIVE SELECTS
        let selects = Array.from(document.querySelectorAll('select')).filter(e => e.offsetParent !== null);
            let solvedAny = false;
            let limit = Math.min(selects.length, answers.length);
            for(let i=0; i<limit; i++) {
                let sel = selects[i];
                let ansLow = answers[i].trim().toLowerCase();
                let matchedOpt = Array.from(sel.options).find(o => o.innerText.trim().toLowerCase() === ansLow);
                if (matchedOpt) {
                    sel.value = matchedOpt.value;
                    sel.dispatchEvent(new Event('change', {bubbles: true}));
                    doneCount++;
                    solvedAny = true;
                }
            }
            if(solvedAny) { callback(doneCount > 0); return; }
        }
        
        // ESTRATEGIA 4: MULTIPLE CHOICE POR BLOQUES ESTRICTOS (RADIO BUTTONS)
        let blocks = Array.from(document.querySelectorAll('.qti-choiceInteraction, fieldset, .question-container, .multiple-choice, .radiogroup')).filter(e => e.offsetParent !== null);
        if (blocks.length > 0) {
            let solvedAny = false;
            let limit = Math.min(blocks.length, answers.length);
            for(let i=0; i<limit; i++) {
                let block = blocks[i];
                let ansLow = answers[i].trim().toLowerCase();
                let opts = Array.from(block.querySelectorAll('label, div, span, button')).filter(e => e.children.length <= 2 && (e.innerText||"").trim().toLowerCase() === ansLow);
                if (opts.length > 0) {
                    opts.sort((a,b) => (b.tagName==='LABEL'?1:0) - (a.tagName==='LABEL'?1:0));
                    supremeClick(opts[0]);
                    let internalInput = opts[0].querySelector('input');
                    if (internalInput) supremeClick(internalInput);
                    doneCount++;
                    solvedAny = true;
                    await new Promise(r => setTimeout(r, 150));
                }
            }
            if(solvedAny) { callback(doneCount > 0); return; }
        }
        
        // ESTRATEGIA 5: FALLBACK (Búsqueda Greedy Global para Click-to-Fill sueltos)
        let allNodes = Array.from(document.querySelectorAll('span, div, button, li, label, p, a'));
        for(let ans of answers) {
            if(!ans) continue;
            let ansLower = ans.trim().toLowerCase();
            let candidates = allNodes.filter(e => e.children.length <= 2 && (e.innerText||"").trim().toLowerCase() === ansLower);
            
            candidates.sort((a,b) => {
                let scoreA = (a.tagName==='LABEL'||a.tagName==='BUTTON'||a.className.includes('choice')||a.className.includes('option')||a.className.includes('radio'))?1:0;
                let scoreB = (b.tagName==='LABEL'||b.tagName==='BUTTON'||b.className.includes('choice')||b.className.includes('option')||b.className.includes('radio'))?1:0;
                return scoreB - scoreA;
            });
            
            if(candidates.length > 0) {
                let target = candidates[0];
                supremeClick(target);
                if(target.tagName === 'LABEL') {
                    let inp = target.querySelector('input');
                    if(inp) supremeClick(inp);
                }
                allNodes = allNodes.filter(n => n !== target);
                doneCount++;
                await new Promise(r => setTimeout(r, 200));
            }
        }
        
        callback(doneCount > 0);
    }
    fillFast();
    """
    
    if frame_elemento is not None:
        try:
            driver.switch_to.frame(frame_elemento)
        except Exception:
            # Si el frame es stale (viejo), intentamos buscarlo de nuevo por RAM
            _, nuevo_frame = get_ajax_data_directly(driver)
            if nuevo_frame:
                try:
                    driver.switch_to.frame(nuevo_frame)
                except:
                    return False
            else:
                return False
        
    # Usamos execute_async_script para que Python se congele hasta que los clics JS terminen secuencialmente
    driver.set_script_timeout(10) # 10 segundos maximo de espera
    try:
        exito = driver.execute_async_script(js_code, respuestas_planas)
    except Exception as e:
        print(f"   ⚠️ Error en inyección JS: {e}")
        exito = False
    
    try:
        driver.switch_to.default_content()
    except:
        pass
        
    return exito


# =========================================
# 🔹 CHECK ANSWERS + NEXT (CAMBRIDGE ONE)
# =========================================

def click_check_answers(driver, frame_elemento):
    """Hace click en el botón 'Check Answers' de Cambridge One."""
    js_code = """
    let callback = arguments[0];
    async function doCheck() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }
        
        let btn = document.querySelector('a.btn.btn-check') 
               || document.querySelector('a[data-event="check"]')
               || document.querySelector('button.btn-check')
               || document.querySelector('[class*="check"]');
        
        if (!btn) {
            let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            btn = allBtns.find(b => {
                let text = (b.textContent || '').trim().toLowerCase();
                return text.includes('check') && b.offsetParent !== null;
            });
        }
        
        if (btn) {
            supremeClick(btn);
            await new Promise(r => setTimeout(r, 2000)); // Antes 1500
            callback(true);
        } else {
            callback(false);
        }
    }
    doCheck();
    """
    return _ejecutar_en_frame(driver, frame_elemento, js_code)


def click_forward(driver, frame_elemento):
    """Hace click en el botón 'Forward/Next' de Cambridge One."""
    js_code = """
    let callback = arguments[0];
    async function doNext() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }
        
        let btn = document.querySelector('a#btn_forward')
               || document.querySelector('a.btn.btn-next')
               || document.querySelector('a.btn.btn-forward')
               || document.querySelector('[data-action="forward"]');
        
        if (!btn) {
            let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            btn = allBtns.find(b => {
                let aria = (b.getAttribute('aria-label') || '').toLowerCase();
                let cls = (b.className || '').toLowerCase();
                return (aria.includes('forward') || aria.includes('next') || 
                        cls.includes('forward') || cls.includes('next')) && 
                       b.offsetParent !== null;
            });
        }
        
        if (btn) {
            supremeClick(btn);
            await new Promise(r => setTimeout(r, 2500)); // Antes 1500
            callback(true);
        } else {
            callback(false);
        }
    }
    doNext();
    """
    return _ejecutar_en_frame(driver, frame_elemento, js_code)


def _ejecutar_en_frame(driver, frame_elemento, js_code):
    """Ejecuta JS primero dentro del iframe, y si falla, en el parent."""
    if frame_elemento:
        try:
            driver.switch_to.frame(frame_elemento)
        except:
            _, nuevo_frame = get_ajax_data_directly(driver)
            if nuevo_frame:
                try: driver.switch_to.frame(nuevo_frame)
                except: pass
    
    driver.set_script_timeout(10)
    result = False
    try:
        result = driver.execute_async_script(js_code)
    except:
        pass
    
    try: driver.switch_to.default_content()
    except: pass
    
    # Si no funcionó dentro del iframe, intentar en el parent
    if not result:
        driver.set_script_timeout(10)
        try:
            result = driver.execute_async_script(js_code)
        except:
            pass
    
    return result


# =========================================
# 🔹 CLICK "NEXT ACTIVITY" EN PANTALLA DE RESULTADOS
# =========================================

def click_next_activity(driver):
    """Hace click en 'Next activity' en la pantalla de resultados (Amazing!)."""
    # Este botón está FUERA del iframe, en el documento principal
    try:
        driver.switch_to.default_content()
    except:
        pass
    
    js_code = """
    let callback = arguments[0];
    async function doNextActivity() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }
        
        // Buscar botón "Next activity" en el documento principal
        let btn = document.querySelector('a.nextActivityBtn')
               || document.querySelector('button.nextActivityBtn')
               || document.querySelector('[class*="nextActivity"]');
        
        if (!btn) {
            // Buscar por texto
            let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"]'));
            btn = allBtns.find(b => {
                let text = (b.textContent || '').trim().toLowerCase();
                return text.includes('next activity') && b.offsetParent !== null;
            });
        }
        
        if (btn) {
            supremeClick(btn);
            await new Promise(r => setTimeout(r, 2000));
            callback(true);
        } else {
            callback(false);
        }
    }
    doNextActivity();
    """
    
    driver.set_script_timeout(10)
    result = False
    
    # Intentar en main document
    try:
        result = driver.execute_async_script(js_code)
    except:
        pass
    
    if not result:
        # Intentar dentro del iframe
        _, frame = get_ajax_data_directly(driver)
        if frame:
            try:
                driver.switch_to.frame(frame)
                result = driver.execute_async_script(js_code)
                driver.switch_to.default_content()
            except:
                try: driver.switch_to.default_content()
                except: pass
    
    return result


def click_next_button_bottom(driver, frame_elemento):
    """Hace click en el botón azul 'Next' que aparece al fondo de pantallas de presentación."""
    js_code = """
    let callback = arguments[0];
    async function doNext() {
        function supremeClick(el) {
            if(!el) return;
            ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, {bubbles:true, cancelable:true, view:window}));
            });
        }
        
        // Buscar botón "Next" azul al fondo
        let btn = null;
        let allBtns = Array.from(document.querySelectorAll('a, button, [role="button"], input[type="button"]'));
        
        // Primero intentar selector específico de Cambridge
        btn = document.querySelector('a.btn.btn-next-activity')
           || document.querySelector('a.btn-next')
           || document.querySelector('button.btn-next');
        
        if (!btn) {
            btn = allBtns.find(b => {
                let text = (b.textContent || '').trim().toLowerCase();
                return text === 'next' && b.offsetParent !== null;
            });
        }
        
        if (btn) {
            supremeClick(btn);
            await new Promise(r => setTimeout(r, 1500));
            callback(true);
        } else {
            callback(false);
        }
    }
    doNext();
    """
    return _ejecutar_en_frame(driver, frame_elemento, js_code)


def detectar_pantalla_resultados(driver):
    """Detecta si estamos en la pantalla de resultados (Amazing!/Well done!/etc.)"""
    try:
        driver.switch_to.default_content()
    except:
        pass
    
    # Buscar en ambos contextos
    js_code = """
    let text = document.body ? document.body.innerText : '';
    return text.includes('You scored') || text.includes('Amazing') || 
           text.includes('Well done') || text.includes('Good try') ||
           text.includes('Next activity') || text.includes('Start again');
    """
    
    try:
        # Buscar en main document
        result = driver.execute_script(js_code)
        if result:
            return True
    except:
        pass
    
    # Buscar en iframe
    _, frame = get_ajax_data_directly(driver)
    if frame:
        try:
            driver.switch_to.frame(frame)
            result = driver.execute_script(js_code)
            driver.switch_to.default_content()
            if result:
                return True
        except:
            try: driver.switch_to.default_content()
            except: pass
    
    return False


# =========================================
# 🔹 RESOLVER UN EJERCICIO COMPLETO
# =========================================

def resolver_ejercicio(driver):
    """Resuelve un ejercicio completo (todas sus pantallas). 
    Retorna True si se completó exitosamente."""
    
    print("🔍 Extrayendo data del ejercicio...")
    time.sleep(2)
    
    data_dict, frame_elemento = get_ajax_data_directly(driver)
    
    if not data_dict:
        print("❌ No se encontró data. Esperando más tiempo...")
        time.sleep(3)
        data_dict, frame_elemento = get_ajax_data_directly(driver)
        if not data_dict:
            print("❌ No hay data disponible. Puede ser una pantalla sin ejercicio.")
            return False
    
    estructura = []
    if "LearningObjectInfo.xml" in data_dict:
        estructura = parse_learning_object(data_dict["LearningObjectInfo.xml"])
    
    if not estructura:
        print("⚠️ No se detectaron pantallas en este ejercicio.")
        return False

    # Preparar respuestas por pantalla
    respuestas_por_pantalla = []
    for idx, pantalla in enumerate(estructura):
        nombre = pantalla["archivo"]
        tipo = pantalla["tipo"]
        respuestas = []
        if nombre in data_dict:
            q = parse_question(data_dict[nombre], nombre, tipo)
            if q:
                if "sub_preguntas" in q:
                    for sub in q["sub_preguntas"]:
                        respuestas.extend(sub["correctas"])
                else:
                    respuestas.extend(q.get("correctas", []))
        respuestas = [r for r in respuestas if isinstance(r, str) and r.strip()]
        respuestas_por_pantalla.append(respuestas)
    
    total = len(estructura)
    con_respuestas = sum(1 for r in respuestas_por_pantalla if r)
    print(f"🏆 {total} pantallas, {con_respuestas} con respuestas")
    
    # ===== AUTO-RESOLVER TODAS LAS PANTALLAS =====
    for idx in range(total):
        pantalla = estructura[idx]
        respuestas = respuestas_por_pantalla[idx]
        
        if not respuestas:
            print(f"  ⏭️  Pantalla {idx+1}/{total}: Presentación → Avanzando...")
            # Intentar primero el forward, si no funciona, el botón "Next" azul
            fwd_ok = click_forward(driver, frame_elemento)
            if not fwd_ok:
                click_next_button_bottom(driver, frame_elemento)
            time.sleep(2)
            _, frame_elemento = get_ajax_data_directly(driver)
            continue
        
        print(f"\n  🎯 Pantalla {idx+1}/{total}: Resolviendo ({len(respuestas)} respuestas)")
        for i, r in enumerate(respuestas, 1):
            print(f"     [{i}] {r}")
        
        # Paso 1: Llenar respuestas
        time.sleep(1)
        exito = resolver_pantalla_js(driver, frame_elemento, respuestas)
        if exito:
            print(f"     ✅ Respuestas ingresadas")
        else:
            print(f"     ⚠️ Posible fallo al ingresar respuestas")
        
        # Paso 2: Click en Check Answers
        time.sleep(1.5)
        check_ok = click_check_answers(driver, frame_elemento)
        if check_ok:
            print(f"     ✅ Check Answers clickeado")
        else:
            print(f"     ⚠️ No se encontró botón Check Answers")
        
        # Paso 3: Avanzar
        time.sleep(3) # Antes 2
        next_ok = click_forward(driver, frame_elemento)
        if next_ok:
            print(f"     ➡️  Avanzando...")
        else:
            # Intentar botón "Next" azul como fallback
            click_next_button_bottom(driver, frame_elemento)
        
        time.sleep(3.5) # Antes 2.5
        _, frame_elemento = get_ajax_data_directly(driver)
    
    # Verificar si llegamos a la pantalla de resultados
    time.sleep(2)
    if detectar_pantalla_resultados(driver):
        print("  🌟 ¡Pantalla de resultados detectada!")
        return True
    
    # Si no se detectó automáticamente, intentar avanzar una vez más
    click_forward(driver, frame_elemento)
    time.sleep(2)
    click_next_button_bottom(driver, frame_elemento)
    time.sleep(2)
    
    return detectar_pantalla_resultados(driver)


# =========================================
# 🔹 PROCESAR TODO
# =========================================

def main():
    print("=" * 60)
    print("🤖 Bot Cambridge One - Resolución Automática Multi-Ejercicio")
    print("=" * 60)
    options = Options()
    
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    
    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        print("\n❌ ERROR: No se pudo conectar a Brave.")
        print("💡 Para usar el Brave donde tienes tus cuentas, debes cerrarlo y abrirlo en 'modo depuración'.")
        print("He creado un archivo llamado 'lanzar_brave_debug.bat' en esta carpeta.")
        print("双 Doble click a 'lanzar_brave_debug.bat' y luego vuelve a ejecutar este bot.\n")
        return
    
    print("\n✅ ¡Conectado exitosamente a tu Brave principal!")
    print("1) Anda a la pestaña donde tienes abierta tu actividad de Cambridge One.")
    print("2) Navega a la PRIMERA pantalla del ejercicio.")
    print("3) Manten abierta esta consola.\n")

    while True:
        accion = input("\n📝 Presiona ENTER para resolver TODOS los ejercicios automáticamente (o 'q' para salir): ").strip()
        if accion.lower() == 'q':
            break
        
        os.system('cls' if os.name == 'nt' else 'clear')
        print("🔍 Buscando la pestaña de Cambridge...")
        
        encontrado = False
        handles = driver.window_handles
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                if "cambridgeone.org" in driver.current_url:
                    encontrado = True
                    break
            except:
                continue
                
        if not encontrado:
            print("❌ ¡No estás en la pestaña de Cambridge One! Ve a la actividad y presiona ENTER de nuevo.")
            continue
        
        ejercicio_num = 0
        seguir = True
        
        while seguir:
            ejercicio_num += 1
            print(f"\n{'='*50}")
            print(f"📚 EJERCICIO #{ejercicio_num}")
            print(f"{'='*50}")
            
            completado = resolver_ejercicio(driver)
            
            if completado:
                print(f"\n🎉 ¡Ejercicio #{ejercicio_num} COMPLETADO!")
                
                # Intentar ir al siguiente ejercicio
                print("🔄 Buscando 'Next activity'...")
                time.sleep(2)
                next_activity_ok = click_next_activity(driver)
                
                if next_activity_ok:
                    print("➡️  Navegando al siguiente ejercicio...")
                    time.sleep(5)  # Dar tiempo para que cargue el nuevo ejercicio
                    
                    # Verificar que se cargó un nuevo ejercicio (tiene ajaxData)
                    time.sleep(3)
                    test_data, _ = get_ajax_data_directly(driver)
                    if test_data:
                        print("✅ Nuevo ejercicio detectado. Continuando...")
                        continue  # Resolver el siguiente ejercicio
                    else:
                        print("⚠️ No se detectó un nuevo ejercicio. Puede ser una presentación.")
                        # Intentar una vez más después de esperar
                        time.sleep(5)
                        test_data, _ = get_ajax_data_directly(driver)
                        if test_data:
                            continue
                        else:
                            print("⏹️ No hay más ejercicios con data extraíble.")
                            seguir = False
                else:
                    print("⏹️ No se encontró botón 'Next activity'. Puede que hayas terminado la lección.")
                    seguir = False
            else:
                print(f"⚠️ No se pudo completar el ejercicio #{ejercicio_num}.")
                print("   Puede ser una presentación o un tipo de ejercicio no soportado.")
                
                # Intentar avanzar de todas formas
                print("🔄 Intentando ir al siguiente ejercicio...")
                time.sleep(2)
                next_activity_ok = click_next_activity(driver)
                if next_activity_ok:
                    print("➡️  Navegando al siguiente ejercicio...")
                    time.sleep(5)
                    continue
                else:
                    seguir = False
        
        print("\n" + "=" * 50)
        print(f"🏁 SESIÓN TERMINADA: {ejercicio_num} ejercicio(s) procesados")
        print("=" * 50)
        print("\n💡 Navega a otro ejercicio y presiona ENTER para continuar.")

if __name__ == "__main__":
    main()
