"""
Dock para Windows (PySide6).

Atalho de teclado: Ctrl+Alt+D mostra/esconde a dock quando ela esta escondida.
Preferencias: botao direito na dock > Preferencias (salvas em dock_prefs.json).
"""
import atexit
import bisect
import ctypes
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import winreg
import xml.etree.ElementTree as ET
from ctypes import wintypes
from PySide6.QtWidgets import (QApplication, QWidget, QFileIconProvider, QMenu, QFileDialog,
                               QInputDialog, QMessageBox, QVBoxLayout, QHBoxLayout, QLabel,
                               QSlider, QPushButton, QScrollArea, QFrame, QComboBox)
from PySide6.QtCore import (Qt, QTimer, QFileInfo, QSize, QRectF, QPointF, QRect, QPoint,
                            QPropertyAnimation, QEasingCurve, QAbstractNativeEventFilter,
                            QDate, QTime, Signal, QVariantAnimation)
from PySide6.QtGui import (QGuiApplication, QPainter, QColor, QPen, QFont, QFontMetrics, QCursor,
                           QPixmap, QLinearGradient, QBrush, QPolygonF, QImageReader,
                           QPainterPath, QTransform)

# ---------- AJUSTES (pode mexer nestes valores) ----------
BASE = 48            # tamanho normal do icone
ZOOM = 64            # tamanho maximo ao passar o mouse
SLOT = 62            # espaco de cada icone
COR_FUNDO = QColor(255, 255, 255, 40)   # vidro (ultimo numero = transparencia, 0 a 255)
COR_BORDA = QColor(255, 255, 255, 110)  # borda clara
INTERVALO_JANELAS = 600                 # de quanto em quanto tempo (ms) procura apps abertos
ATRASO_PREVIA = 0.22                    # segundos com o mouse parado no icone ate abrir a previa
GRACA_PREVIA = 0.25                     # segundos que a previa espera antes de fechar
MAX_JANELAS_PREVIA = 6                  # maximo de janelas mostradas na previa
AUTO_OCULTAR = True                     # esconde a dock sozinha (veja OCULTAR_QUANDO)
OCULTAR_QUANDO = "maximizada"           # "nunca" | "maximizada" | "tela_cheia" | "sobrepor"
ATRASO_BORDA = 0.12                     # segundos com o mouse na borda de baixo para a dock aparecer
ATRASO_SUMIR = 0.40                     # segundos fora da dock para ela sumir de novo
OCULTAR_BARRA_PRINCIPAL = True          # esconde a barra de tarefas do Windows SO no monitor principal
# (os valores acima sao os padroes; as Preferencias do botao direito sobrescrevem alguns deles)
# ----------------------------------------------------------

PAD_X = 14
PAD_Y = 12
TIP_H_BASE = 56      # espaco minimo em cima para a etiqueta com o nome
TIP_H = TIP_H_BASE   # (cresce sozinho quando o zoom e grande)
RAIO = 24            # arredondamento da dock
MARGEM = 12          # distancia da borda de baixo da tela
SIDE = 50            # espaco nas laterais para a etiqueta nao cortar
PILL_H = BASE + PAD_Y * 2
ALCANCE_ZOOM = 2.3   # ate quantos icones de distancia o zoom se espalha (curva de sino, como no macOS)
RANGE = SLOT * ALCANCE_ZOOM
# mola da animacao (quanto maior K, mais rapido; quanto maior C, menos "balanca")
MOLA_K = 520.0
MOLA_C = 34.0
LIMIAR_ARRASTO = 6   # pixels que o mouse precisa andar para comecar a arrastar

PROGRAMAS = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs"
USUARIO = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")

VERSAO = "1.0.3"                 # suba este numero a cada atualizacao (a tag do GitHub tem que ser igual: v1.0.1...)
GITHUB_REPO = "paulodoscriativoscontato-collab/dock"                 # "seu-usuario/Dock": de onde a dock baixa as atualizacoes (vazio = sem atualizacao)

PASTA = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))      # True quando roda como Dock.exe instalado
# instalado em "Arquivos de Programas" nao da para gravar ao lado do programa: configs ficam em %APPDATA%\Dock
DADOS = os.path.join(os.environ.get("APPDATA") or PASTA, "Dock") if FROZEN else PASTA
try:
    os.makedirs(DADOS, exist_ok=True)
except Exception:
    DADOS = PASTA
CONFIG = os.path.join(DADOS, "dock_config.json")
PREFS_ARQ = os.path.join(DADOS, "dock_prefs.json")
PASTA_ICONES_STORE = os.path.join(DADOS, "icones_store")
ARQ_ERRO = os.path.join(DADOS, "erro_dock.log")

try:
    import atualizador                # verificacao/download de atualizacoes (atualizador.py, ao lado deste arquivo)
except Exception:
    atualizador = None


def _registrar_erro(tipo, valor, tb):
    """Como a dock roda sem janela de console, qualquer erro fica salvo em erro_dock.log."""
    try:
        import traceback
        with open(ARQ_ERRO, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write("".join(traceback.format_exception(tipo, valor, tb)) + "\n")
    except Exception:
        pass


sys.excepthook = _registrar_erro

# Lista usada apenas na primeira vez (depois tudo fica salvo no dock_config.json)
TIPOS_SISTEMA = ("prefs", "lixeira", "painel", "relogio")

PADRAO = [
    {"caminho": os.path.join(PROGRAMAS, "Adobe Photoshop 2026.lnk"), "nome": "Photoshop"},
    {"caminho": os.path.join(PROGRAMAS, "Adobe Premiere Pro 2026.lnk"), "nome": "Premiere Pro"},
    {"caminho": os.path.join(PROGRAMAS, "Adobe After Effects 2025.lnk"), "nome": "After Effects"},
    {"caminho": os.path.join(USUARIO, "Vivaldi.lnk"), "nome": "Vivaldi"},
    {"caminho": os.path.join(USUARIO, "Notion.lnk"), "nome": "Notion"},
]

provider = None

# ---------- preferencias (botao direito > Preferencias...) ----------
PREFS_PADRAO = {
    "tema": "escuro",                # escuro | claro | auto (segue o Windows)
    "vidro": True,                   # desfoque atras da dock
    "vidro_modo": "cantos_pequenos",  # cantos_pequenos | acrilico | simples
    "vidro_opacidade": 20,           # 0 (transparente) a 100 (solido)
    "borda_pct": 100,                # intensidade da borda clara do vidro (0 a 150)
    "animacao_pct": 100,             # velocidade das animacoes (50 a 200)
    "cantos_px": 24,
    "tamanho_px": 48,
    "zoom_pct": 190,
    "espaco_px": 14,
    "margem_px": 12,
    "ocultar_quando": OCULTAR_QUANDO,
    "ocultar_barra": OCULTAR_BARRA_PRINCIPAL,
    "previa": True,
    "mostrar_abertos": True,         # apps abertos que nao estao fixados
    "bolinhas": True,                # bolinha de "app aberto"
    "pulinho": True,                 # pulinho do icone ao abrir
    "etiquetas": True,               # nome ao passar o mouse
    "engrenagem": True,              # engrenagem que abre as Preferencias
    "todas_telas": True,             # uma dock em cada monitor
    "atualizar_auto": True,          # baixa e instala atualizacoes sozinha
    "ordem_sistema": ["prefs", "lixeira", "painel", "relogio"],   # ordem dos itens de sistema (arraste na dock)
    "lixeira": True,
    "painel": True,
    "relogio": True,
    "relogio_24h": True,
    "relogio_data": True,
}

# combinacoes prontas (Preferencias > Aparencia > Estilo do vidro)
PRESETS_VIDRO = {
    "cristal": {"vidro_opacidade": 6, "borda_pct": 120},
    "equilibrado": {"vidro_opacidade": 20, "borda_pct": 100},
    "fosco": {"vidro_opacidade": 55, "borda_pct": 80},
}

TEMAS = {
    "escuro": {
        "dark": True,
        "tint": (22, 22, 26),
        "borda": QColor(255, 255, 255, 60),
        "texto": QColor(255, 255, 255),
        "texto2": QColor(255, 255, 255, 160),
        "chip": QColor(255, 255, 255, 26),
        "sep": QColor(255, 255, 255, 55),
        "etq_bg": QColor(30, 30, 33, 235),
        "etq_borda": QColor(255, 255, 255, 60),
        "popup_bg": QColor(32, 32, 35),
        "popup_hover": QColor(255, 255, 255, 30),
        "popup_thumb": QColor(0, 0, 0, 100),
        "popup_borda": QColor(255, 255, 255, 45),
        "bateria_baixa": QColor(255, 110, 100),
        "bateria_ok": QColor(120, 220, 120),
    },
    "claro": {
        "dark": False,
        "tint": (250, 250, 252),
        "borda": QColor(255, 255, 255, 170),
        "texto": QColor(28, 28, 30),
        "texto2": QColor(28, 28, 30, 150),
        "chip": QColor(0, 0, 0, 22),
        "sep": QColor(0, 0, 0, 55),
        "etq_bg": QColor(250, 250, 252, 240),
        "etq_borda": QColor(0, 0, 0, 40),
        "popup_bg": QColor(246, 246, 248),
        "popup_hover": QColor(0, 0, 0, 22),
        "popup_thumb": QColor(0, 0, 0, 24),
        "popup_borda": QColor(0, 0, 0, 40),
        "bateria_baixa": QColor(220, 60, 50),
        "bateria_ok": QColor(40, 170, 70),
    },
}


def sistema_claro():
    """True se o Windows esta no tema claro (para o tema 'auto')."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except Exception:
        return False


def nome_tema():
    t = PREFS["tema"]
    if t == "auto":
        return "claro" if sistema_claro() else "escuro"
    return t if t in TEMAS else "escuro"


def tema_atual():
    return TEMAS[nome_tema()]


def cor_borda():
    """Cor da borda do vidro, ja com a intensidade escolhida nas Preferencias."""
    c = QColor(tema_atual()["borda"])
    c.setAlpha(max(0, min(255, int(c.alpha() * PREFS["borda_pct"] / 100.0))))
    return c


def vel_anim(base):
    """Fator de suavizacao das animacoes, ajustado pela velocidade das Preferencias."""
    return max(0.05, min(0.9, base * PREFS["animacao_pct"] / 100.0))


def carregar_prefs():
    prefs = dict(PREFS_PADRAO)
    try:
        with open(PREFS_ARQ, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if isinstance(d, dict):
            for k, v in prefs.items():
                if k in d and type(d[k]) is type(v):
                    prefs[k] = d[k]
            if "borda_pct" not in d:
                # arquivo salvo por uma versao antiga: aplica o vidro mais transparente
                prefs["vidro_opacidade"] = PREFS_PADRAO["vidro_opacidade"]
    except Exception:
        pass
    return prefs


def salvar_prefs():
    try:
        with open(PREFS_ARQ, "w", encoding="utf-8") as f:
            json.dump(PREFS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erro ao salvar preferencias: {e}")


def aplicar_prefs():
    global BASE, ZOOM, SLOT, PILL_H, RANGE, OCULTAR_QUANDO, TIP_H, RAIO, MARGEM
    BASE = max(24, min(96, int(PREFS["tamanho_px"])))
    ZOOM = max(BASE, int(BASE * PREFS["zoom_pct"] / 100))
    SLOT = BASE + int(PREFS["espaco_px"])
    PILL_H = BASE + PAD_Y * 2
    RANGE = SLOT * ALCANCE_ZOOM
    TIP_H =TIP_H_BASE + max(0, (ZOOM - BASE) + 8)       # espaco extra para a barra crescer e a etiqueta
    RAIO = int(PREFS["cantos_px"])
    MARGEM = int(PREFS["margem_px"])
    OCULTAR_QUANDO = PREFS["ocultar_quando"]


PREFS = carregar_prefs()
aplicar_prefs()

# ---------- Windows (janelas abertas, previa, barra de tarefas, atalhos) ----------
CHAVE_INICIO = "::inicio"
CACHE_ICONES = {}
_icone_inicio = None
_cache_pid = {}
ID_TECLA = 0xD0C1

# programas do proprio Windows que nao devem aparecer como "app aberto"
EXES_IGNORADOS = {
    "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
    "shellexperiencehost.exe", "textinputhost.exe", "lockapp.exe",
    "applicationframehost.exe", "widgets.exe", "systemsettingsbroker.exe",
}
CLASSES_IGNORADAS = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
EXES_SHELL = EXES_IGNORADOS - {"applicationframehost.exe"}

user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")
dwmapi = ctypes.WinDLL("dwmapi")
gdi32 = ctypes.WinDLL("gdi32")
shell32 = ctypes.WinDLL("shell32")

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
gdi32.CreateRectRgn.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("rcDestination", wintypes.RECT),
        ("rcSource", wintypes.RECT),
        ("opacity", ctypes.c_ubyte),
        ("fVisible", wintypes.BOOL),
        ("fSourceClientAreaOnly", wintypes.BOOL),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_void_p),
    ]


class SHQUERYRBINFO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong),
                ("i64NumItems", ctypes.c_longlong)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
dwmapi.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(wintypes.HANDLE)]
dwmapi.DwmUnregisterThumbnail.argtypes = [wintypes.HANDLE]
dwmapi.DwmUpdateThumbnailProperties.argtypes = [wintypes.HANDLE, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
dwmapi.DwmQueryThumbnailSourceSize.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.SIZE)]
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.SetWindowCompositionAttribute.argtypes = [wintypes.HWND, ctypes.POINTER(WINCOMPATTRDATA)]
shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
shell32.SHAppBarMessage.restype = ctypes.c_size_t
shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
shell32.SHQueryRecycleBinW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SHQUERYRBINFO)]
shell32.SHEmptyRecycleBinW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
kernel32.GetSystemPowerStatus.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]


def classe_da_janela(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def pid_da_janela(hwnd):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def exe_do_processo(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        tam = wintypes.DWORD(1024)
        ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(tam))
        return buf.value if ok else None
    finally:
        kernel32.CloseHandle(h)


def exe_do_pid(pid):
    exe = _cache_pid.get(pid)
    if exe is None:
        exe = exe_do_processo(pid) or ""
        if len(_cache_pid) > 300:
            _cache_pid.clear()
        _cache_pid[pid] = exe
    return exe or None


def exe_uwp(hwnd, pid_host):
    """Apps da Microsoft Store ficam dentro do ApplicationFrameHost; acha o processo real."""
    achado = []

    def cb(h, l):
        if classe_da_janela(h) == "Windows.UI.Core.CoreWindow":
            p = pid_da_janela(h)
            if p != pid_host:
                achado.append(p)
                return False
        return True

    fn = WNDENUMPROC(cb)
    user32.EnumChildWindows(hwnd, fn, 0)
    return exe_do_pid(achado[0]) if achado else None


def deve_esconder_dock(dock_rect, dock_hwnd):
    """dock_rect = (esq, topo, dir, base) da dock em pixels reais; dock_hwnd = janela da dock.
    Decide se a janela da frente (que esteja no MESMO monitor da dock) pede a dock escondida."""
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return False
        if classe_da_janela(hwnd) in CLASSES_IGNORADAS:
            return False
        pid = pid_da_janela(hwnd)
        if pid == os.getpid():
            return False
        exe = exe_do_pid(pid)
        if exe and os.path.basename(exe).lower() in EXES_SHELL:
            return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        mon_janela = user32.MonitorFromWindow(hwnd, 2)
        if not user32.GetMonitorInfoW(mon_janela, ctypes.byref(mi)):
            return False
        if mon_janela != user32.MonitorFromWindow(dock_hwnd, 2):   # so vale para o monitor desta dock
            return False
        m = mi.rcMonitor
        cobre = (rect.left <= m.left and rect.top <= m.top
                 and rect.right >= m.right and rect.bottom >= m.bottom)

        if OCULTAR_QUANDO == "sobrepor":
            e, t, d, b = dock_rect
            return rect.left < d and rect.right > e and rect.top < b and rect.bottom > t
        if OCULTAR_QUANDO == "tela_cheia":
            estilo = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
            if (estilo & 0xC00000) == 0xC00000:       # tem barra de titulo = maximizada comum
                return False
            return cobre
        return cobre or bool(user32.IsZoomed(hwnd))   # "maximizada"
    except Exception:
        return False


# ---------- barra de tarefas do Windows (monitor principal) ----------
ARQ_BARRA = os.path.join(DADOS, "barra_backup.json")
_barra_ativa = False


def _estado_barra(hwnd):
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    abd.hWnd = hwnd
    return int(shell32.SHAppBarMessage(4, ctypes.byref(abd)))      # ABM_GETSTATE


def _definir_estado_barra(hwnd, estado):
    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    abd.hWnd = hwnd
    abd.lParam = estado
    shell32.SHAppBarMessage(0xA, ctypes.byref(abd))                # ABM_SETSTATE


def esconder_barra():
    """Tira a barra do monitor principal (Shell_TrayWnd). A dos outros monitores nao e escondida."""
    global _barra_ativa
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        return False
    original = _estado_barra(hwnd)
    if not os.path.exists(ARQ_BARRA):                  # guarda o estado original uma vez so
        try:
            with open(ARQ_BARRA, "w", encoding="utf-8") as f:
                json.dump({"estado": original}, f)
        except Exception:
            pass
    _definir_estado_barra(hwnd, original | 1)          # oculta automaticamente: libera o espaco dela na tela
    _barra_ativa = True
    manter_barra()
    return True


def manter_barra():
    """Se o Windows mostrar a barra de novo (mouse na borda, Iniciar...), esconde na hora."""
    if not _barra_ativa:
        return
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    if hwnd and user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, 0)                     # SW_HIDE


def restaurar_barra(forcar=False):
    """Mostra a barra de tarefas de volta e devolve a configuracao original dela."""
    global _barra_ativa
    if not _barra_ativa and not forcar:
        return
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    if hwnd:
        user32.ShowWindow(hwnd, 5)                     # SW_SHOW
    try:
        with open(ARQ_BARRA, "r", encoding="utf-8") as f:
            estado = int(json.load(f)["estado"])
        if hwnd:
            _definir_estado_barra(hwnd, estado)
        os.remove(ARQ_BARRA)
    except Exception:
        pass
    _barra_ativa = False


# ---------- janelas ----------
def norm(caminho):
    return os.path.normcase(os.path.normpath(caminho))


def nome_bonito(exe):
    stem = os.path.splitext(os.path.basename(exe))[0]
    return stem[:1].upper() + stem[1:] if stem.islower() else stem


def listar_janelas():
    """Lista de todas as janelas de apps abertos, da mais de cima para a mais de baixo."""
    resultado = []
    meu_pid = os.getpid()

    def cb(hwnd, l):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, 4):                 # tem dono (janela auxiliar)
                return True
            if user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            ex = user32.GetWindowLongW(hwnd, -20)
            if (ex & 0x80) and not (ex & 0x40000):        # TOOLWINDOW sem APPWINDOW
                return True
            cloaked = ctypes.c_int(0)
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
            if cloaked.value:                             # janela "escondida" do Windows
                return True
            if classe_da_janela(hwnd) in CLASSES_IGNORADAS:
                return True
            pid = pid_da_janela(hwnd)
            if pid == meu_pid:
                return True
            exe = exe_do_pid(pid)
            if not exe:
                return True
            if os.path.basename(exe).lower() == "applicationframehost.exe":
                exe = exe_uwp(hwnd, pid)
                if not exe:
                    return True
            if os.path.basename(exe).lower() in EXES_IGNORADOS:
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            resultado.append({"hwnd": hwnd, "exe": norm(exe), "real": exe, "titulo": buf.value})
        except Exception:
            pass
        return True

    fn = WNDENUMPROC(cb)
    user32.EnumWindows(fn, 0)
    return resultado


def janela_combina(j, exes, nomes):
    if j["exe"] in exes:
        return True
    stem = os.path.splitext(os.path.basename(j["exe"]))[0]
    return stem in nomes


def e_spotify(janelas):
    return bool(janelas) and os.path.splitext(os.path.basename(janelas[0]["exe"]))[0] == "spotify"


def trazer_janela(hwnd):
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                        # SW_RESTORE
    # truque do Alt: o Windows so deixa trazer a janela para frente depois de uma tecla
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    return True


def ativar_janelas(janelas):
    """Leva para a frente a janela mais recente do app. Se ela ja esta na frente, alterna para a proxima."""
    hwnds = [j["hwnd"] for j in janelas]
    if not hwnds:
        return False
    alvo = hwnds[0]
    fg = user32.GetForegroundWindow()
    if len(hwnds) > 1 and fg in hwnds and not user32.IsIconic(fg):
        alvo = hwnds[(hwnds.index(fg) + 1) % len(hwnds)]
    return trazer_janela(alvo)


def fechar_janela(hwnd):
    user32.PostMessageW(hwnd, 0x10, 0, 0)                 # WM_CLOSE


# ---------- teclas do sistema ----------
def enviar_win(tecla=None):
    user32.keybd_event(0x5B, 0, 0, 0)   # tecla Windows apertada
    if tecla:
        user32.keybd_event(tecla, 0, 0, 0)
        user32.keybd_event(tecla, 0, 2, 0)
    user32.keybd_event(0x5B, 0, 2, 0)   # tecla Windows solta


def abrir_menu_iniciar():
    enviar_win()


def abrir_painel_rapido():
    enviar_win(0x41)                    # Win + A


def abrir_central_notificacoes():
    enviar_win(0x4E)                    # Win + N


def tecla_midia(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)


def estado_bateria():
    """(porcentagem, na_tomada) ou None se o PC nao tem bateria."""
    try:
        st = SYSTEM_POWER_STATUS()
        if not kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None
        if st.BatteryFlag == 128 or st.BatteryLifePercent == 255:
            return None
        return int(st.BatteryLifePercent), st.ACLineStatus == 1
    except Exception:
        return None


# ---------- lixeira ----------
def mover_para_lixeira(caminhos):
    caminhos = [os.path.normpath(c) for c in caminhos if c]
    if not caminhos:
        return
    buf = ctypes.create_unicode_buffer("\0".join(caminhos) + "\0\0")
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3                                           # FO_DELETE
    op.pFrom = ctypes.addressof(buf)
    op.fFlags = 0x40                                       # FOF_ALLOWUNDO (vai para a Lixeira)
    shell32.SHFileOperationW(ctypes.byref(op))


def lixeira_tem_itens():
    try:
        info = SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
        if shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
            return info.i64NumItems > 0
    except Exception:
        pass
    return False


def esvaziar_lixeira():
    try:
        shell32.SHEmptyRecycleBinW(None, None, 0)
    except Exception:
        pass


# ---------- vidro (desfoque atras da dock) ----------
def ativar_desfoque(hwnd, tint=(255, 255, 255), alpha=60):
    """Acrilico atras da janela. 'tint' e a cor e 'alpha' (0-255) o quanto ela cobre o desfoque."""
    try:
        r, g, b = tint
        cor = ((int(alpha) & 0xFF) << 24) | (b << 16) | (g << 8) | r      # formato AABBGGRR
        accent = ACCENT_POLICY(4, 0, cor, 0)                              # ACCENT_ENABLE_ACRYLICBLURBEHIND
        dados = WINCOMPATTRDATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                                ctypes.sizeof(accent))
        user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(dados))
    except Exception:
        pass


RAIO_DWM = 8   # raio fixo dos cantos que o proprio Windows 11 aplica a janela


def raio_efetivo():
    """Com desfoque do Windows, o acrilico ignora o recorte da janela; o jeito de ter cantos
    arredondados de verdade e usar o arredondamento do proprio Windows (raio pequeno e fixo)."""
    if PREFS["vidro"] and PREFS.get("vidro_modo", "cantos_pequenos") == "cantos_pequenos":
        return RAIO_DWM
    return RAIO


def vidro_com_desfoque():
    return bool(PREFS["vidro"]) and PREFS.get("vidro_modo", "cantos_pequenos") != "simples"


def cantos_dwm(hwnd, ligar):
    """Windows 11: DWMWCP_ROUND (2) arredonda a janela inclusive o acrilico; 1 = sem arredondar."""
    try:
        pref = ctypes.c_int(2 if ligar else 1)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
    except Exception:
        pass


def alpha_vidro():
    """0 (cristalino) a 100 (fosco). A curva da mais precisao perto do transparente."""
    v = max(0, min(100, int(PREFS["vidro_opacidade"]))) / 100.0
    return max(1, int(255 * (v ** 1.6)))


_logs_regiao = 0


def definir_regiao(hwnd, w, h, raio, dpr):
    """Recorta a janela de vidro em retangulo arredondado. Usa o tamanho REAL da janela em pixels
    (GetClientRect); w/h/dpr so servem de reserva se isso falhar."""
    global _logs_regiao
    try:
        r = wintypes.RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(r)) and r.right > 0 and r.bottom > 0:
            wpx, hpx = r.right - r.left, r.bottom - r.top
        else:
            wpx, hpx = int(w * dpr), int(h * dpr)
        rgn = gdi32.CreateRoundRectRgn(0, 0, wpx + 1, hpx + 1, int(raio * 2 * dpr), int(raio * 2 * dpr))
        ok = user32.SetWindowRgn(hwnd, rgn, True)
        if _logs_regiao < 6:                      # diagnostico: o Windows aceitou o recorte?
            _logs_regiao += 1
            tmp = gdi32.CreateRectRgn(0, 0, 0, 0)
            tipo = user32.GetWindowRgn(hwnd, tmp)  # 0 = sem recorte, 1-3 = tem recorte
            gdi32.DeleteObject(tmp)
            try:
                with open(os.path.join(DADOS, "prefs_debug.log"), "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} vidro: regiao {wpx}x{hpx} raio={raio} dpr={dpr} "
                            f"SetWindowRgn={ok} GetWindowRgn={tipo}\n")
            except Exception:
                pass
    except Exception:
        _registrar_erro(*sys.exc_info())


# ---------- iniciar com o Windows (por app) ----------
def pasta_startup():
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Windows\Start Menu\Programs\Startup")


def atalho_startup(nome):
    limpo = "".join(c for c in nome if c not in '<>:"/\\|?*').strip() or "App"
    return os.path.join(pasta_startup(), limpo + ".lnk")


def inicia_com_windows(nome):
    return os.path.exists(atalho_startup(nome))


def alternar_inicio(caminho, nome):
    destino = atalho_startup(nome)
    try:
        if os.path.exists(destino):
            os.remove(destino)
            return
        os.makedirs(pasta_startup(), exist_ok=True)
        if caminho.lower().endswith(".lnk"):
            shutil.copy2(caminho, destino)
            return
        if caminho.lower().startswith("shell:"):
            alvo, args = "explorer.exe", caminho
        else:
            alvo, args = caminho, ""
        q = lambda t: t.replace("'", "''")
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(destino)}');"
                  f"$s.TargetPath='{q(alvo)}';$s.Arguments='{q(args)}';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       creationflags=0x08000000, timeout=20)
    except Exception as e:
        print(f"Erro ao alterar inicio com o Windows: {e}")


def atalho_dock_startup():
    return os.path.join(pasta_startup(), "Dock.lnk")


def inicio_dock_ativo():
    return os.path.exists(atalho_dock_startup())


def definir_inicio_dock(ativo):
    destino = atalho_dock_startup()
    try:
        if not ativo:
            if os.path.exists(destino):
                os.remove(destino)
            return
        os.makedirs(pasta_startup(), exist_ok=True)
        q = lambda t: t.replace("'", "''")
        if FROZEN:                                   # instalada: o atalho aponta para o proprio Dock.exe
            alvo, pasta_alvo = sys.executable, os.path.dirname(sys.executable)
        else:
            alvo, pasta_alvo = os.path.join(PASTA, "iniciar.vbs"), PASTA
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(destino)}');"
                  f"$s.TargetPath='{q(alvo)}';$s.WorkingDirectory='{q(pasta_alvo)}';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       creationflags=0x08000000, timeout=20)
    except Exception as e:
        print(f"Erro ao alterar inicio da dock com o Windows: {e}")


# ---------- apps da Microsoft Store ----------
def listar_apps_store():
    script = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$pk = @{}
Get-AppxPackage | ForEach-Object { $pk[$_.PackageFamilyName] = $_.InstallLocation }
$apps = Get-StartApps | Where-Object { $_.AppID -like '*!*' }
$lista = foreach ($a in $apps) {
  $partes = $a.AppID -split '!'
  [pscustomobject]@{ Nome = $a.Name; AUMID = $a.AppID; AppId = $partes[1]; Pasta = $pk[$partes[0]] }
}
@($lista) | ConvertTo-Json -Compress
'''
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, timeout=90, creationflags=0x08000000)
    txt = r.stdout.decode("utf-8", errors="ignore").strip()
    dados = json.loads(txt) if txt else []
    if isinstance(dados, dict):
        dados = [dados]
    return [d for d in (dados or []) if d.get("Nome") and d.get("AUMID")]


def montar_icone_store(pasta, logo, cor, chave):
    if not logo:
        return None
    rel = logo.replace("/", "\\")
    base_dir = os.path.dirname(os.path.join(pasta, rel))
    stem = os.path.splitext(os.path.basename(rel))[0].lower()
    try:
        candidatos = [f for f in os.listdir(base_dir)
                      if f.lower().startswith(stem) and f.lower().endswith(".png")]
    except Exception:
        return None
    melhor, melhor_pts, sem_fundo = None, -1, False
    for f in candidatos:
        low = f.lower()
        if "contrast" in low or "lightunplated" in low:
            continue
        caminho = os.path.join(base_dir, f)
        sz = QImageReader(caminho).size()
        if not sz.isValid():
            continue
        pts = sz.width() + (500 if "altform-unplated" in low else 0)
        if pts > melhor_pts:
            melhor, melhor_pts, sem_fundo = caminho, pts, "altform-unplated" in low
    if not melhor:
        return None
    img = QPixmap(melhor)
    if img.isNull():
        return None
    pix = QPixmap(128, 128)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    if sem_fundo:
        c = QColor(cor) if cor else QColor()
        if not c.isValid() or c.alpha() == 0:
            c = QColor(45, 45, 55)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(4, 4, 120, 120), 28, 28)
        lado = 84
    else:
        lado = 120
    esc = img.scaled(lado, lado, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p.drawPixmap((128 - esc.width()) // 2, (128 - esc.height()) // 2, esc)
    p.end()
    try:
        os.makedirs(PASTA_ICONES_STORE, exist_ok=True)
        destino = os.path.join(PASTA_ICONES_STORE, hashlib.md5(chave.encode("utf-8")).hexdigest() + ".png")
        pix.save(destino, "PNG")
        return destino
    except Exception:
        return None


def dados_app_store(app):
    """(caminho_do_icone_png, caminho_do_exe) lendo o manifesto do app."""
    pasta, appid = app.get("Pasta"), app.get("AppId")
    if not pasta or not os.path.isdir(pasta):
        return None, None
    try:
        raiz = ET.parse(os.path.join(pasta, "AppxManifest.xml")).getroot()
    except Exception:
        return None, None
    exe, icone = None, None
    for el in raiz.iter():
        if el.tag.split("}")[-1] != "Application" or el.get("Id") != appid:
            continue
        if el.get("Executable"):
            exe = os.path.join(pasta, el.get("Executable"))
        for ve in el.iter():
            if ve.tag.split("}")[-1] == "VisualElements":
                logo = ve.get("Square44x44Logo") or ve.get("Square150x150Logo")
                icone = montar_icone_store(pasta, logo, ve.get("BackgroundColor"), app["AUMID"])
                break
        break
    return icone, exe


# ---------- config da dock ----------
def carregar():
    try:
        with open(CONFIG, "r", encoding="utf-8-sig") as f:
            dados = json.load(f)
        if isinstance(dados, list):
            return dados
    except Exception:
        pass
    return padrao_inicial()


def padrao_inicial():
    """Apps da dock na primeira vez: so os que existem neste PC (cada pessoa tem programas diferentes)."""
    win = os.environ.get("WINDIR", r"C:\Windows")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    candidatos = [
        {"caminho": os.path.join(win, "explorer.exe"), "nome": "Explorador de Arquivos"},
        {"caminho": os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"), "nome": "Microsoft Edge"},
        {"caminho": os.path.join(pf, r"Google\Chrome\Application\chrome.exe"), "nome": "Google Chrome"},
        {"caminho": os.path.join(pf, r"Mozilla Firefox\firefox.exe"), "nome": "Firefox"},
    ] + [dict(d) for d in PADRAO]
    achados = [d for d in candidatos if os.path.exists(d["caminho"])]
    return achados or [candidatos[0]]


def salvar(dados):
    try:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erro ao salvar: {e}")


def pegar_icone(caminho):
    info = QFileInfo(caminho)
    alvo = info.symLinkTarget()
    if alvo and os.path.exists(alvo):
        return provider.icon(QFileInfo(alvo))
    return provider.icon(info)


def pix_para(caminho):
    if caminho not in CACHE_ICONES:
        icone = pegar_icone(caminho)
        CACHE_ICONES[caminho] = None if icone.isNull() else icone.pixmap(QSize(128, 128))
    return CACHE_ICONES[caminho]


def pix_item(d):
    ic = d.get("icone")
    if ic and os.path.exists(ic):
        if ic not in CACHE_ICONES:
            CACHE_ICONES[ic] = QPixmap(ic)
        return CACHE_ICONES[ic]
    return pix_para(d.get("caminho", ""))


def pasta_do_item(d):
    """Se o item da dock e uma pasta (ou atalho para pasta), devolve o caminho dela."""
    c = d.get("caminho", "")
    if not c or c.lower().startswith("shell:"):
        return None
    try:
        if os.path.isdir(c):
            return c
        if c.lower().endswith(".lnk"):
            alvo = QFileInfo(c).symLinkTarget()
            if alvo and os.path.isdir(alvo):
                return alvo
    except Exception:
        pass
    return None


def chaves_de(d):
    """O que identifica um programa fixado (para achar as janelas dele)."""
    caminho = d.get("caminho", "")
    exes, nomes = set(), set()
    alvo = QFileInfo(caminho).symLinkTarget()
    if alvo:
        exes.add(norm(alvo))
    exes.add(norm(caminho))
    if d.get("exe"):
        exes.add(norm(d["exe"]))
        nomes.add(os.path.splitext(os.path.basename(d["exe"]))[0].lower())
    base = os.path.splitext(os.path.basename(caminho))[0].lower()
    if base and not caminho.lower().startswith("shell:"):
        nomes.add(base)
    if d.get("nome"):
        nomes.add(d["nome"].lower())
    return exes, nomes


# ---------- icones desenhados ----------
_cache_lixeira = {}


def icone_lixeira(cheia):
    if cheia in _cache_lixeira:
        return _cache_lixeira[cheia]
    pix = QPixmap(128, 128)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    corpo = QLinearGradient(0, 30, 0, 118)
    corpo.setColorAt(0, QColor(225, 228, 235, 235))
    corpo.setColorAt(1, QColor(150, 156, 170, 235))
    if cheia:                                              # papeis saindo da lixeira
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(245, 245, 250))
        p.save()
        p.translate(50, 36)
        p.rotate(-12)
        p.drawRoundedRect(QRectF(-12, -16, 34, 40), 3, 3)
        p.restore()
        p.save()
        p.translate(78, 36)
        p.rotate(10)
        p.setBrush(QColor(255, 236, 160))
        p.drawRoundedRect(QRectF(-14, -16, 34, 40), 3, 3)
        p.restore()
    p.setPen(QPen(QColor(255, 255, 255, 120), 2))
    p.setBrush(QBrush(corpo))
    p.drawRoundedRect(QRectF(28, 40, 72, 76), 10, 10)       # corpo
    p.setBrush(QColor(235, 238, 245))
    p.drawRoundedRect(QRectF(20, 28, 88, 14), 6, 6)         # tampa
    p.drawRoundedRect(QRectF(50, 18, 28, 12), 5, 5)         # alca
    p.setPen(QPen(QColor(110, 116, 130, 200), 4, Qt.SolidLine, Qt.RoundCap))
    for x in (50, 64, 78):
        p.drawLine(x, 56, x, 100)
    p.end()
    _cache_lixeira[cheia] = pix
    return pix


_icone_prefs = None


def icone_engrenagem():
    """Engrenagem cinza (estilo Ajustes do macOS) que abre as Preferencias da dock."""
    global _icone_prefs
    if _icone_prefs is None:
        pix = QPixmap(128, 128)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 0, 128)
        g.setColorAt(0, QColor(196, 200, 210))
        g.setColorAt(1, QColor(120, 124, 136))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(6, 6, 116, 116), 27, 27)
        engr = QPainterPath()
        engr.addEllipse(QPointF(64, 64), 30, 30)
        for k in range(8):
            t = QTransform()
            t.translate(64, 64)
            t.rotate(k * 45)
            dente = QPainterPath()
            dente.addRoundedRect(QRectF(-8, -45, 16, 24), 3, 3)
            engr = engr.united(t.map(dente))
        furo = QPainterPath()
        furo.addEllipse(QPointF(64, 64), 12, 12)
        engr = engr.subtracted(furo)
        p.setBrush(QColor(50, 54, 64))
        p.drawPath(engr)
        p.end()
        _icone_prefs = pix
    return _icone_prefs


def icone_windows():
    global _icone_inicio
    if _icone_inicio is None:
        pix = QPixmap(128, 128)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 128, 128)
        g.setColorAt(0, QColor(90, 190, 255))
        g.setColorAt(1, QColor(0, 100, 205))
        p.setBrush(QBrush(g))
        p.setPen(Qt.NoPen)
        lado, gap = 50, 6
        ini = (128 - (lado * 2 + gap)) / 2
        for r in range(2):
            for c in range(2):
                p.drawRoundedRect(
                    QRectF(ini + c * (lado + gap), ini + r * (lado + gap), lado, lado), 4, 4)
        p.end()
        _icone_inicio = pix
    return _icone_inicio


# ---------- pilhas (conteudo recente de pastas) ----------
IMAGENS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def listar_recentes(pasta, n):
    itens = []
    try:
        with os.scandir(pasta) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    st = e.stat()
                    if getattr(st, "st_file_attributes", 0) & 0x2:   # oculto
                        continue
                    itens.append((st.st_mtime, e.path, e.name))
                except Exception:
                    continue
    except Exception:
        return []
    itens.sort(reverse=True)
    return [(c, nome) for _, c, nome in itens[:n]]


def miniatura(caminho):
    if os.path.splitext(caminho)[1].lower() in IMAGENS:
        try:
            leitor = QImageReader(caminho)
            leitor.setAutoTransform(True)
            sz = leitor.size()
            if sz.isValid():
                sz.scale(64, 64, Qt.KeepAspectRatio)
                leitor.setScaledSize(sz)
            img = leitor.read()
            if not img.isNull():
                return QPixmap.fromImage(img)
        except Exception:
            pass
    icone = provider.icon(QFileInfo(caminho))
    return None if icone.isNull() else icone.pixmap(QSize(64, 64))


def com_alpha(cor, a):
    c = QColor(cor)
    c.setAlpha(int(a))
    return c


# ---------- janelinha de previa (estilo Apple) ----------
class Previa(QWidget):
    def __init__(self, dock):
        super().__init__()
        self.dock = dock
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

        self.chave = None
        self.nome = ""
        self.pix = None
        self.media = False     # mostra os controles do Spotify
        self.janelas = []
        self.cartoes = []      # (cartao, miniatura, linha_do_titulo)
        self.btns = []         # botoes de midia (anterior, play/pausa, proxima)
        self.thumbs = []       # handles das miniaturas do DWM (None se falhou)
        self.hover = -1
        self.hover_x = False
        self.hover_btn = -1
        self.anchor_x = 0
        self.y_base = 0
        self.tela = QRect(0, 0, 1920, 1080)
        self._decorado = False

        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(170)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

    # ---------- layout ----------
    def calcular(self):
        self.janelas = self.janelas[:MAX_JANELAS_PREVIA]
        n = max(1, len(self.janelas))
        pad, cp, gap = 12, 8, 10
        disp = self.tela.width() - 40
        tw = 220
        while tw > 120 and pad * 2 + n * (tw + cp * 2) + (n - 1) * gap > disp:
            tw -= 10
        th = int(tw * 0.62)
        linha_h = 22
        ch = cp * 2 + th + 4 + linha_h
        tc = n * (tw + cp * 2) + (n - 1) * gap
        w = max(tc + pad * 2, 190 if self.media else 124)
        x = (w - tc) // 2
        self.cartoes = []
        for _ in range(len(self.janelas)):
            card = QRect(x, pad, tw + cp * 2, ch)
            thumb = QRect(x + cp, pad + cp, tw, th)
            linha = QRect(x + cp, pad + cp + th + 4, tw, linha_h)
            self.cartoes.append((card, thumb, linha))
            x += tw + cp * 2 + gap
        h = pad * 2 + ch
        self.btns = []
        if self.media:
            tam, esp = 34, 16
            total = 3 * tam + 2 * esp
            x0 = (w - total) // 2
            y0 = pad + ch + 6
            for k in range(3):
                self.btns.append(QRect(x0 + k * (tam + esp), y0, tam, tam))
            h += 44
        self.resize(w, h)

    def posicao(self):
        x = int(self.anchor_x - self.width() / 2)
        x = max(self.tela.left() + 8, min(x, self.tela.right() - self.width() - 8))
        y = int(self.y_base - self.height())
        return x, y

    def configurar(self, it, anchor_x, y_base, tela):
        self.anchor_x = anchor_x
        self.y_base = y_base
        self.tela = tela
        hwnds = [j["hwnd"] for j in it["janelas"]][:MAX_JANELAS_PREVIA]
        media = e_spotify(it["janelas"])
        mesma = (self.isVisible() and self.chave == it["chave"] and media == self.media
                 and hwnds == [j["hwnd"] for j in self.janelas])
        self.chave = it["chave"]
        self.nome = it["nome"]
        self.pix = it["pix"]
        self.media = media
        self.janelas = list(it["janelas"])
        if mesma:
            self.janelas = self.janelas[:MAX_JANELAS_PREVIA]
            x, y = self.posicao()
            if not self.anim.state():
                self.move(x, y)
            self.update()
            return
        self.reconstruir(primeira=not self.isVisible())

    def atualizar(self, janelas):
        """Chamado pelo dock quando a lista de janelas (ou os titulos) muda."""
        novos = [j["hwnd"] for j in janelas][:MAX_JANELAS_PREVIA]
        if novos == [j["hwnd"] for j in self.janelas]:
            self.janelas = list(janelas)[:MAX_JANELAS_PREVIA]
            self.update()
        else:
            self.janelas = list(janelas)
            self.reconstruir(primeira=False)

    def reconstruir(self, primeira):
        self.calcular()
        x, y = self.posicao()
        if primeira:
            self.move(x, y + 10)
            self.show()
            self._decorar()
            self.anim.stop()
            self.anim.setStartValue(QPoint(x, y + 10))
            self.anim.setEndValue(QPoint(x, y))
            self.anim.start()
        else:
            self.anim.stop()
            self.move(x, y)
        self.registrar_thumbs()
        self.update()

    def esconder(self):
        if not self.isVisible():
            return
        self.anim.stop()
        self.liberar_thumbs()
        self.hide()
        self.chave = None
        self.hover = -1
        self.hover_btn = -1

    # ---------- DWM (miniaturas ao vivo + cantos arredondados) ----------
    def _decorar(self):
        if self._decorado:
            return
        self._decorado = True
        try:
            hwnd = int(self.winId())
            pref = ctypes.c_int(2)                         # DWMWCP_ROUND
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            cor = ctypes.c_uint(0x00505050)                # borda sutil
            dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(cor), 4)
            m = MARGINS(1, 1, 1, 1)                        # sombra do sistema
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
        except Exception:
            pass

    def liberar_thumbs(self):
        for t in self.thumbs:
            if t is not None:
                try:
                    dwmapi.DwmUnregisterThumbnail(t)
                except Exception:
                    pass
        self.thumbs = []

    def registrar_thumbs(self):
        self.liberar_thumbs()
        try:
            dest = int(self.winId())
        except Exception:
            return
        dpr = self.devicePixelRatioF()
        for j, (card, thumb, linha) in zip(self.janelas, self.cartoes):
            h = wintypes.HANDLE()
            try:
                ok = dwmapi.DwmRegisterThumbnail(dest, j["hwnd"], ctypes.byref(h))
            except Exception:
                ok = 1
            if ok != 0 or not h.value:
                self.thumbs.append(None)
                continue
            sz = wintypes.SIZE()
            if dwmapi.DwmQueryThumbnailSourceSize(h, ctypes.byref(sz)) != 0 or sz.cx <= 0 or sz.cy <= 0:
                dwmapi.DwmUnregisterThumbnail(h)
                self.thumbs.append(None)
                continue
            tw, th = thumb.width() * dpr, thumb.height() * dpr
            esc = min(tw / sz.cx, th / sz.cy, 1.0)
            w, hh = int(sz.cx * esc), int(sz.cy * esc)
            px = int(thumb.x() * dpr + (tw - w) / 2)
            py = int(thumb.y() * dpr + (th - hh) / 2)
            props = DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = 0x1 | 0x4 | 0x8                # destino | opacidade | visivel
            props.rcDestination = wintypes.RECT(px, py, px + w, py + hh)
            props.opacity = 255
            props.fVisible = True
            dwmapi.DwmUpdateThumbnailProperties(h, ctypes.byref(props))
            self.thumbs.append(h)

    # ---------- mouse ----------
    def _idx(self, pos):
        for i, (card, _, _) in enumerate(self.cartoes):
            if card.contains(pos):
                return i
        return -1

    def _idx_btn(self, pos):
        for i, r in enumerate(self.btns):
            if r.contains(pos):
                return i
        return -1

    @staticmethod
    def _rect_fechar(linha):
        return QRect(linha.right() - 17, linha.top() + (linha.height() - 18) // 2, 18, 18)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        i = self._idx(pos)
        hx = i >= 0 and self._rect_fechar(self.cartoes[i][2]).contains(pos)
        hb = self._idx_btn(pos)
        if i != self.hover or hx != self.hover_x or hb != self.hover_btn:
            self.hover, self.hover_x, self.hover_btn = i, hx, hb
            self.update()

    def leaveEvent(self, event):
        self.hover, self.hover_x, self.hover_btn = -1, False, -1
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        b = self._idx_btn(pos)
        if b >= 0:
            tecla_midia((0xB1, 0xB3, 0xB0)[b])             # anterior | play/pausa | proxima
            return
        i = self._idx(pos)
        if i < 0 or i >= len(self.janelas):
            return
        hwnd = self.janelas[i]["hwnd"]
        if self._rect_fechar(self.cartoes[i][2]).contains(pos):
            fechar_janela(hwnd)
            del self.janelas[i]
            self.hover, self.hover_x = -1, False
            if not self.janelas:
                self.esconder()
            else:
                self.reconstruir(primeira=False)
        else:
            trazer_janela(hwnd)
            self.esconder()

    # ---------- desenho ----------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        T = tema_atual()
        p.fillRect(self.rect(), T["popup_bg"])

        fonte = QFont("Segoe UI", 9)
        p.setFont(fonte)
        fm = QFontMetrics(fonte)

        for i, (card, thumb, linha) in enumerate(self.cartoes):
            if i >= len(self.janelas):
                break
            j = self.janelas[i]
            ativo = (i == self.hover)

            p.setPen(Qt.NoPen)
            if ativo:
                p.setBrush(T["popup_hover"])
                p.drawRoundedRect(card, 12, 12)
            p.setBrush(T["popup_thumb"])
            p.drawRoundedRect(thumb, 7, 7)

            sem_thumb = i >= len(self.thumbs) or self.thumbs[i] is None
            if sem_thumb and self.pix is not None:
                alvo = QRect(thumb.center().x() - 24, thumb.center().y() - 24, 48, 48)
                p.drawPixmap(alvo, self.pix, self.pix.rect())

            if self.pix is not None:
                p.drawPixmap(QRect(linha.left(), linha.top() + (linha.height() - 16) // 2, 16, 16),
                             self.pix, self.pix.rect())

            texto_rect = QRect(linha.left() + 22, linha.top(), linha.width() - 22 - 24, linha.height())
            titulo = j["titulo"] or self.nome
            p.setPen(com_alpha(T["texto"], 240 if ativo else 190))
            p.drawText(texto_rect, Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(titulo, Qt.ElideRight, texto_rect.width()))

            if ativo:
                r = self._rect_fechar(linha)
                p.setPen(Qt.NoPen)
                p.setBrush(com_alpha(T["texto"], 70 if self.hover_x else 35))
                p.drawEllipse(r)
                p.setPen(QPen(com_alpha(T["texto"], 235), 1.6, Qt.SolidLine, Qt.RoundCap))
                c = r.center()
                d = 3.6
                p.drawLine(QPointF(c.x() - d + 0.5, c.y() - d + 0.5), QPointF(c.x() + d + 0.5, c.y() + d + 0.5))
                p.drawLine(QPointF(c.x() - d + 0.5, c.y() + d + 0.5), QPointF(c.x() + d + 0.5, c.y() - d + 0.5))

        if self.media and self.btns:
            titulo = self.janelas[0]["titulo"] if self.janelas else ""
            tocando = bool(titulo) and not titulo.lower().startswith("spotify")
            branco = com_alpha(T["texto"], 240)
            for k, r in enumerate(self.btns):
                p.setPen(Qt.NoPen)
                p.setBrush(com_alpha(T["texto"], 62 if k == self.hover_btn else 28))
                p.drawEllipse(r)
                c = r.center()
                cx, cy = c.x() + 0.5, c.y() + 0.5
                p.setBrush(branco)
                if k == 1:
                    if tocando:                                  # pausa
                        p.drawRoundedRect(QRectF(cx - 6, cy - 7, 4.5, 14), 1.5, 1.5)
                        p.drawRoundedRect(QRectF(cx + 1.5, cy - 7, 4.5, 14), 1.5, 1.5)
                    else:                                        # play
                        p.drawPolygon(QPolygonF([QPointF(cx - 5, cy - 8), QPointF(cx - 5, cy + 8),
                                                 QPointF(cx + 8, cy)]))
                else:
                    sinal = -1 if k == 0 else 1
                    for dx in (-5, 3):
                        xs = cx + dx * sinal
                        p.drawPolygon(QPolygonF([QPointF(xs - 4 * sinal, cy - 6), QPointF(xs - 4 * sinal, cy + 6),
                                                 QPointF(xs + 4 * sinal, cy)]))


# ---------- fundo de vidro atras da dock ----------
class Vidro(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._tam = (0, 0)
        # reaplica recorte + vidro quando o tamanho para de mudar (o Windows as vezes perde o recorte
        # enquanto a janela esta sendo redimensionada a cada quadro da animacao)
        self._deb = QTimer(self)
        self._deb.setSingleShot(True)
        self._deb.setInterval(90)
        self._deb.timeout.connect(self._reforcar)

    def ajustar(self, x, y, w, h):
        self.setGeometry(x, y, w, h)
        if self._tam != (w, h):
            self._tam = (w, h)
            if self.isVisible():
                self._regiao()
                self._deb.start()

    def _reforcar(self):
        if self.isVisible():
            self._regiao()
            self.aplicar_cor()

    def _regiao(self):
        definir_regiao(int(self.winId()), self.width(), self.height(), raio_efetivo(), self.devicePixelRatioF())

    def aplicar_cor(self):
        hwnd = int(self.winId())
        cantos_dwm(hwnd, PREFS.get("vidro_modo") == "cantos_pequenos")
        ativar_desfoque(hwnd, tema_atual()["tint"], alpha_vidro())

    def atualizar_estilo(self):
        """Chamado quando muda tema, transparencia ou cantos."""
        if self.isVisible():
            self._regiao()
            self.aplicar_cor()

    def showEvent(self, event):
        super().showEvent(event)
        self._regiao()
        self.aplicar_cor()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)


# ---------- pilha: conteudo recente de uma pasta ----------
class Pilha(QWidget):
    def __init__(self, dock):
        super().__init__()
        self.dock = dock
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.pasta = ""
        self.titulo = ""
        self.celulas = []          # (retangulo, caminho, nome, pixmap)
        self.hover = -1
        self.hover_abrir = False
        self.rect_abrir = QRect()

    def abrir(self, pasta, titulo, anchor_x, y_base, tela):
        self.pasta, self.titulo = pasta, titulo
        arquivos = listar_recentes(pasta, 12)
        cols, cw, ch, pad, cab = 4, 96, 92, 14, 30
        n = len(arquivos)
        linhas = max(1, -(-n // cols))
        cols_usadas = max(3, min(cols, n)) if n else 3
        w = pad * 2 + cols_usadas * cw
        fy = pad + cab + linhas * ch + 6
        h = fy + 28 + pad
        self.celulas = []
        for i, (caminho, nome) in enumerate(arquivos):
            r = QRect(pad + (i % cols) * cw, pad + cab + (i // cols) * ch, cw, ch)
            self.celulas.append((r, caminho, nome, miniatura(caminho)))
        self.rect_abrir = QRect(pad, fy, w - pad * 2, 28)
        self.hover, self.hover_abrir = -1, False
        self.resize(w, h)
        x = int(anchor_x - w / 2)
        x = max(tela.left() + 8, min(x, tela.right() - w - 8))
        self.move(x, int(y_base - h))
        self.show()

    def _idx(self, pos):
        for i, (r, _, _, _) in enumerate(self.celulas):
            if r.contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        i = self._idx(pos)
        a = self.rect_abrir.contains(pos)
        if i != self.hover or a != self.hover_abrir:
            self.hover, self.hover_abrir = i, a
            self.update()

    def leaveEvent(self, event):
        self.hover, self.hover_abrir = -1, False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        try:
            if self.rect_abrir.contains(pos):
                os.startfile(self.pasta)
                self.close()
                return
            i = self._idx(pos)
            if i >= 0:
                os.startfile(self.celulas[i][1])
                self.close()
        except Exception as e:
            print(f"Erro ao abrir: {e}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        T = tema_atual()
        p.setPen(QPen(T["popup_borda"], 1))
        p.setBrush(com_alpha(T["popup_bg"], 244))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)

        f = QFont("Segoe UI", 10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(com_alpha(T["texto"], 235))
        p.drawText(QRect(14, 12, self.width() - 28, 26), Qt.AlignLeft | Qt.AlignVCenter, self.titulo)

        if not self.celulas:
            p.setFont(QFont("Segoe UI", 9))
            p.setPen(T["texto2"])
            p.drawText(QRect(0, 50, self.width(), 60), Qt.AlignCenter, "Pasta vazia")

        fm_f = QFont("Segoe UI", 8)
        p.setFont(fm_f)
        fm = QFontMetrics(fm_f)
        for i, (r, caminho, nome, pix) in enumerate(self.celulas):
            if i == self.hover:
                p.setPen(Qt.NoPen)
                p.setBrush(T["popup_hover"])
                p.drawRoundedRect(r.adjusted(3, 3, -3, -3), 10, 10)
            if pix is not None:
                esc = pix.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(r.center().x() - esc.width() // 2, r.top() + 10 + (52 - esc.height()) // 2, esc)
            p.setPen(com_alpha(T["texto"], 225))
            p.drawText(QRect(r.left() + 4, r.top() + 66, r.width() - 8, 20), Qt.AlignHCenter | Qt.AlignVCenter,
                       fm.elidedText(nome, Qt.ElideMiddle, r.width() - 10))

        p.setPen(Qt.NoPen)
        p.setBrush(com_alpha(T["texto"], 60 if self.hover_abrir else 30))
        p.drawRoundedRect(self.rect_abrir, 9, 9)
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(com_alpha(T["texto"], 240))
        p.drawText(self.rect_abrir, Qt.AlignCenter, "Abrir pasta")


# ---------- atalho global (Ctrl+Alt+D) ----------
class FiltroTecla(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, eventType, message):
        try:
            if bytes(eventType) == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312 and msg.wParam == ID_TECLA:     # WM_HOTKEY
                    self.callback()
                    return True, 0
        except Exception:
            pass
        return False, 0


# ---------- painel de preferencias (estilo Apple) ----------
def paleta():
    if tema_atual()["dark"]:
        return {"bg": "#1c1c1e", "card": "#2c2c2e", "texto": "#f5f5f7", "texto2": "#98989f",
                "sep": "#3a3a3c", "campo": "#3a3a3c", "campo_hover": "#48484a", "acento": "#0a84ff",
                "trilho_off": "#48484a", "seg_sel": "#636366", "seg_texto_sel": "#ffffff"}
    return {"bg": "#f2f2f7", "card": "#ffffff", "texto": "#1d1d1f", "texto2": "#6e6e73",
            "sep": "#e5e5ea", "campo": "#e9e9ee", "campo_hover": "#dcdce2", "acento": "#007aff",
            "trilho_off": "#d1d1d6", "seg_sel": "#ffffff", "seg_texto_sel": "#1d1d1f"}


def qss_painel():
    P = paleta()
    return f"""
    #corpo {{ background: {P['bg']}; }}
    QScrollArea {{ background: {P['bg']}; border: none; }}
    QLabel {{ color: {P['texto']}; font-family: 'Segoe UI'; font-size: 13px; background: transparent; }}
    QLabel#titulo {{ font-size: 26px; font-weight: 700; }}
    QLabel#sub {{ color: {P['texto2']}; font-size: 12px; }}
    QLabel#secao {{ color: {P['texto2']}; font-size: 11px; font-weight: 600; padding: 20px 6px 6px 6px; }}
    QLabel#dica {{ color: {P['texto2']}; font-size: 11px; }}
    QLabel#valor {{ color: {P['texto2']}; font-size: 12px; }}
    QFrame#cartao {{ background: {P['card']}; border-radius: 12px; }}
    QFrame#sep {{ background: {P['sep']}; border: none; }}
    QSlider::groove:horizontal {{ height: 4px; background: {P['trilho_off']}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {P['acento']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #ffffff; width: 20px; height: 20px; margin: -8px 0;
                                  border-radius: 10px; border: 1px solid rgba(0,0,0,60); }}
    QComboBox {{ background: {P['campo']}; color: {P['texto']}; border: none; border-radius: 8px;
                 padding: 6px 28px 6px 12px; min-width: 230px; font-size: 13px; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{ background: {P['card']}; color: {P['texto']}; outline: none;
                                   selection-background-color: {P['acento']}; selection-color: #ffffff;
                                   border: 1px solid {P['sep']}; }}
    QPushButton {{ background: {P['campo']}; color: {P['texto']}; border: none; border-radius: 8px;
                   padding: 9px 18px; font-size: 13px; }}
    QPushButton:hover {{ background: {P['campo_hover']}; }}
    QPushButton#perigo {{ color: #ff453a; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {P['sep']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    """


class Interruptor(QWidget):
    """Interruptor liga/desliga igual ao do iOS."""
    alterado = Signal(bool)

    def __init__(self, ligado=False):
        super().__init__()
        self.setFixedSize(46, 28)
        self.setCursor(Qt.PointingHandCursor)
        self._on = bool(ligado)
        self._t = 1.0 if self._on else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.valueChanged.connect(self._mover)

    def _mover(self, v):
        self._t = float(v)
        self.update()

    def definir(self, v):
        v = bool(v)
        if v == self._on:
            return
        self._on = v
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if v else 0.0)
        self._anim.start()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.definir(not self._on)
            self.alterado.emit(self._on)

    def paintEvent(self, event):
        P = paleta()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor(P["trilho_off"]), QColor("#34c759")
        t = self._t
        cor = QColor(int(off.red() + (on.red() - off.red()) * t),
                     int(off.green() + (on.green() - off.green()) * t),
                     int(off.blue() + (on.blue() - off.blue()) * t))
        p.setPen(Qt.NoPen)
        p.setBrush(cor)
        p.drawRoundedRect(QRectF(0, 0, 46, 28), 14, 14)
        x = 3 + t * (46 - 28)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, 3.8, 22, 22))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(x, 3, 22, 22))


class Segmentado(QWidget):
    """Controle segmentado (ex.: Automatico | Claro | Escuro)."""
    alterado = Signal(str)

    def __init__(self, opcoes, atual):
        super().__init__()
        self.opcoes = opcoes
        self.atual = atual
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        fm = QFontMetrics(QFont("Segoe UI", 9))
        self._larguras = [fm.horizontalAdvance(r) + 30 for _, r in opcoes]
        self.setFixedWidth(sum(self._larguras) + 6)

    def definir(self, v):
        self.atual = v
        self.update()

    def _rects(self):
        x, rs = 3.0, []
        for w in self._larguras:
            rs.append(QRectF(x, 3, w, self.height() - 6))
            x += w
        return rs

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = QPointF(event.position())
        for (valor, _), r in zip(self.opcoes, self._rects()):
            if r.contains(pos) and valor != self.atual:
                self.atual = valor
                self.update()
                self.alterado.emit(valor)
                return

    def paintEvent(self, event):
        P = paleta()
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(P["campo"]))
        p.drawRoundedRect(QRectF(self.rect()), 9, 9)
        p.setFont(QFont("Segoe UI", 9))
        for (valor, rotulo), r in zip(self.opcoes, self._rects()):
            sel = (valor == self.atual)
            if sel:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(P["seg_sel"]))
                p.drawRoundedRect(r, 7, 7)
            p.setPen(QColor(P["seg_texto_sel"] if sel else P["texto"]))
            p.drawText(r, Qt.AlignCenter, rotulo)


class Deslizante(QWidget):
    """Controle deslizante com o valor escrito ao lado."""
    alterado = Signal(int)

    def __init__(self, minimo, maximo, valor, sufixo):
        super().__init__()
        self.sufixo = sufixo
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(minimo, maximo)
        self.s.setValue(int(valor))
        self.s.setFixedWidth(190)
        self.rot = QLabel()
        self.rot.setObjectName("valor")
        self.rot.setFixedWidth(58)
        self.rot.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.s)
        lay.addWidget(self.rot)
        self._rotulo(int(valor))
        self.s.valueChanged.connect(self._mudou)

    def _rotulo(self, v):
        self.rot.setText(f"{v}{self.sufixo}")

    def _mudou(self, v):
        self._rotulo(v)
        self.alterado.emit(v)

    def definir(self, v):
        self.s.blockSignals(True)
        self.s.setValue(int(v))
        self.s.blockSignals(False)
        self._rotulo(int(v))


class ListaPref(QComboBox):
    alterado = Signal(str)

    def __init__(self, opcoes, atual):
        super().__init__()
        for valor, rotulo in opcoes:
            self.addItem(rotulo, valor)
        self.definir(atual)
        self.currentIndexChanged.connect(lambda i: self.alterado.emit(self.itemData(i)))

    def definir(self, v):
        self.blockSignals(True)
        i = self.findData(v)
        if i >= 0:
            self.setCurrentIndex(i)
        self.blockSignals(False)


class PainelPreferencias(QWidget):
    def __init__(self, dock):
        super().__init__()
        self.dock = dock
        self.setWindowTitle("Preferências da Dock")
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowCloseButtonHint
                            | Qt.WindowMinimizeButtonHint)
        self.setFixedWidth(540)
        self.controles = {}
        self._nome_estilo = None
        self._timer_salvar = QTimer(self)
        self._timer_salvar.setSingleShot(True)
        self._timer_salvar.setInterval(350)
        self._timer_salvar.timeout.connect(salvar_prefs)

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        raiz.addWidget(self.scroll)
        self.corpo = QWidget()
        self.corpo.setObjectName("corpo")
        self.scroll.setWidget(self.corpo)
        self.lay = QVBoxLayout(self.corpo)
        self.lay.setContentsMargins(26, 22, 26, 26)
        self.lay.setSpacing(0)
        self._construir()
        self.restilizar()

    # ---------- blocos de interface ----------
    def _secao(self, texto):
        lbl = QLabel(texto.upper())
        lbl.setObjectName("secao")
        self.lay.addWidget(lbl)

    def _linha(self, titulo, controle, dica=None):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(16, 11, 16, 11)
        h.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(QLabel(titulo))
        if dica:
            d = QLabel(dica)
            d.setObjectName("dica")
            d.setWordWrap(True)
            col.addWidget(d)
        h.addLayout(col, 1)
        h.addWidget(controle, 0, Qt.AlignRight | Qt.AlignVCenter)
        return w

    def _cartao(self, linhas):
        f = QFrame()
        f.setObjectName("cartao")
        v = QVBoxLayout(f)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        for i, linha in enumerate(linhas):
            if i:
                cont = QWidget()
                ch = QHBoxLayout(cont)
                ch.setContentsMargins(16, 0, 0, 0)
                sep = QFrame()
                sep.setObjectName("sep")
                sep.setFixedHeight(1)
                ch.addWidget(sep)
                v.addWidget(cont)
            v.addWidget(linha)
        self.lay.addWidget(f)

    def _mudou(self, chave, valor):
        self.dock.definir_pref(chave, valor, persistir=False)
        self._timer_salvar.start()
        if chave == "tema":
            self.restilizar()
        if chave in ("vidro_opacidade", "borda_pct") and hasattr(self, "seg_preset"):
            self.seg_preset.definir(self.preset_atual())

    def preset_atual(self):
        """Nome do estilo de vidro que bate com os valores atuais ('' se for personalizado)."""
        for nome, valores in PRESETS_VIDRO.items():
            if all(PREFS[k] == v for k, v in valores.items()):
                return nome
        return ""

    def aplicar_preset(self, nome):
        valores = PRESETS_VIDRO.get(nome)
        if not valores:
            return
        for chave, valor in valores.items():
            self.dock.definir_pref(chave, valor, persistir=False)
        self._timer_salvar.start()
        self.sincronizar()

    def _sw(self, chave):
        w = Interruptor(PREFS[chave])
        w.alterado.connect(lambda v, c=chave: self._mudou(c, v))
        self.controles[chave] = w
        return w

    def _seg(self, chave, opcoes):
        w = Segmentado(opcoes, PREFS[chave])
        w.alterado.connect(lambda v, c=chave: self._mudou(c, v))
        self.controles[chave] = w
        return w

    def _sl(self, chave, minimo, maximo, sufixo):
        w = Deslizante(minimo, maximo, PREFS[chave], sufixo)
        w.alterado.connect(lambda v, c=chave: self._mudou(c, v))
        self.controles[chave] = w
        return w

    def _lista(self, chave, opcoes):
        w = ListaPref(opcoes, PREFS[chave])
        w.alterado.connect(lambda v, c=chave: self._mudou(c, v))
        self.controles[chave] = w
        return w

    def _construir(self):
        L = self.lay
        titulo = QLabel("Preferências")
        titulo.setObjectName("titulo")
        L.addWidget(titulo)
        sub = QLabel("Personalize a aparência e o comportamento da dock. As mudanças valem na hora.")
        sub.setObjectName("sub")
        sub.setWordWrap(True)
        L.addWidget(sub)

        self.seg_preset = Segmentado([("cristal", "Cristal"), ("equilibrado", "Equilibrado"),
                                      ("fosco", "Fosco")], self.preset_atual())
        self.seg_preset.alterado.connect(self.aplicar_preset)
        self._secao("Aparência")
        self._cartao([
            self._linha("Tema", self._seg("tema", [("auto", "Automático"), ("claro", "Claro"), ("escuro", "Escuro")]),
                        "Automático segue o tema do Windows"),
            self._linha("Efeito de vidro", self._sw("vidro"), "Desfoca o que está atrás da dock"),
            self._linha("Tipo de vidro", self._lista("vidro_modo", [
                ("cantos_pequenos", "Desfoque (cantos pequenos)"),
                ("acrilico", "Desfoque (cantos grandes, pode vazar)"),
                ("simples", "Sem desfoque (cantos grandes)")]),
                "Se o desfoque vazar dos cantos, use um dos outros tipos"),
            self._linha("Estilo do vidro", self.seg_preset,
                        "Combinações prontas de transparência e borda"),
            self._linha("Opacidade do vidro", self._sl("vidro_opacidade", 0, 100, " %"),
                        "Arraste para a esquerda para deixar mais transparente"),
            self._linha("Intensidade da borda", self._sl("borda_pct", 0, 150, " %"),
                        "A linha clara ao redor da dock"),
            self._linha("Cantos arredondados", self._sl("cantos_px", 8, 36, " px")),
        ])

        self._secao("Tamanho e posição")
        self._cartao([
            self._linha("Tamanho dos ícones", self._sl("tamanho_px", 32, 80, " px")),
            self._linha("Zoom ao passar o mouse", self._sl("zoom_pct", 100, 200, " %"),
                        "100 % = sem zoom"),
            self._linha("Espaço entre os ícones", self._sl("espaco_px", 0, 30, " px")),
            self._linha("Distância da borda de baixo", self._sl("margem_px", 0, 48, " px")),
            self._linha("Mostrar em todos os monitores", self._sw("todas_telas"),
                        "Uma dock em cada tela, com os mesmos apps"),
        ])

        self._secao("Comportamento")
        self._cartao([
            self._linha("Esconder a dock", self._lista("ocultar_quando", [
                ("nunca", "Nunca"),
                ("maximizada", "Com janela maximizada ou em tela cheia"),
                ("tela_cheia", "Só em tela cheia"),
                ("sobrepor", "Quando uma janela passar por cima")]),
                "Para mostrar: encoste o mouse na borda de baixo ou use Ctrl+Alt+D"),
            self._linha("Pré-visualização das janelas", self._sw("previa"),
                        "Miniaturas ao vivo ao passar o mouse"),
            self._linha("Mostrar apps abertos", self._sw("mostrar_abertos"),
                        "Inclui apps que não estão fixados na dock"),
            self._linha("Indicador de app aberto", self._sw("bolinhas"), "Bolinha embaixo do ícone"),
            self._linha("Animação ao abrir apps", self._sw("pulinho"), "O ícone dá pulinhos enquanto abre"),
            self._linha("Velocidade das animações", self._sl("animacao_pct", 50, 200, " %"),
                        "Zoom e deslize dos ícones. 100 % = normal"),
            self._linha("Mostrar nomes", self._sw("etiquetas"), "Nome do app ao passar o mouse"),
        ])

        self._secao("Itens da dock")
        self._cartao([
            self._linha("Engrenagem de Preferências", self._sw("engrenagem"), "Atalho para abrir este painel"),
            self._linha("Lixeira", self._sw("lixeira")),
            self._linha("Painel rápido", self._sw("painel"), "Wi-Fi, volume e bateria"),
            self._linha("Relógio", self._sw("relogio")),
            self._linha("Relógio de 24 horas", self._sw("relogio_24h")),
            self._linha("Mostrar a data no relógio", self._sw("relogio_data")),
        ])

        self._secao("Sistema")
        self.sw_inicio = Interruptor(inicio_dock_ativo())
        self.sw_inicio.alterado.connect(definir_inicio_dock)
        self._cartao([
            self._linha("Abrir a dock ao ligar o Windows", self.sw_inicio),
            self._linha("Esconder a barra de tarefas do Windows", self._sw("ocultar_barra"),
                        "Só no monitor principal"),
        ])

        self._secao("Atualizações")
        b_upd = QPushButton("Verificar agora")
        b_upd.clicked.connect(lambda: self.dock.verificar_atualizacao(manual=True))
        self._cartao([
            self._linha("Versão instalada", QLabel(VERSAO)),
            self._linha("Atualizar automaticamente", self._sw("atualizar_auto"),
                        "Baixa e instala as novidades sozinha, em segundo plano"),
            self._linha("Procurar atualizações", b_upd),
        ])

        L.addSpacing(18)
        botoes = QHBoxLayout()
        b1 = QPushButton("Restaurar padrões")
        b1.clicked.connect(self.restaurar_padroes)
        b3 = QPushButton("Abrir pasta da dock")
        b3.clicked.connect(lambda: os.startfile(DADOS))
        b2 = QPushButton("Fechar a dock")
        b2.setObjectName("perigo")
        b2.clicked.connect(QApplication.quit)
        botoes.addWidget(b1)
        botoes.addWidget(b3)
        botoes.addStretch(1)
        botoes.addWidget(b2)
        L.addLayout(botoes)
        L.addStretch(1)

    # ---------- estado ----------
    def sincronizar(self):
        for chave, w in self.controles.items():
            w.definir(PREFS[chave])
        self.seg_preset.definir(self.preset_atual())
        self.sw_inicio.definir(inicio_dock_ativo())

    def restaurar_padroes(self):
        PREFS.clear()
        PREFS.update(PREFS_PADRAO)
        salvar_prefs()
        self.dock.recarregar_prefs()
        self.sincronizar()
        self.restilizar()

    def restilizar(self):
        """Reaplica as cores do painel, mas so quando o tema realmente mudou."""
        nome = nome_tema()
        if nome == self._nome_estilo:
            return
        self._nome_estilo = nome
        self.setStyleSheet(qss_painel())
        try:
            escuro = ctypes.c_int(1 if tema_atual()["dark"] else 0)
            dwmapi.DwmSetWindowAttribute(int(self.winId()), 20, ctypes.byref(escuro), 4)   # barra de titulo escura
        except Exception:
            pass
        for w in self.controles.values():
            w.update()
        self.sw_inicio.update()
        self.update()

    def mostrar(self):
        self.sincronizar()
        self.restilizar()
        # abre no monitor onde o mouse esta (e nao sempre no principal)
        tela = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geom = tela.availableGeometry()
        alt = min(self.corpo.sizeHint().height() + 6, geom.height() - 120)
        self.resize(540, max(360, alt))
        self.move(geom.center().x() - self.width() // 2, geom.top() + 40)
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        try:
            hwnd = int(self.winId())
            trazer_janela(hwnd)
            # a dock fica sempre por cima; deixa o painel por cima dela tambem
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        except Exception:
            _registrar_erro(*sys.exc_info())


class Dock(QWidget):
    """Uma dock por monitor. A do monitor principal e a 'mestre' (cuida das janelas abertas, da barra de
    tarefas, das preferencias e do atalho); as dos outros monitores sao 'irmas' e copiam o estado dela."""

    def __init__(self, tela=None, mestre=None):
        super().__init__()
        self.tela = tela or QGuiApplication.primaryScreen()
        self.mestre = mestre            # None = esta e a dock mestre
        self.irmas = []                 # docks dos outros monitores (so a mestre usa)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        if mestre is not None:          # nasce ja no monitor certo (cada monitor pode ter escala diferente)
            try:
                self.create()
                if self.windowHandle():
                    self.windowHandle().setScreen(self.tela)
            except Exception:
                pass

        self.dados = mestre.dados if mestre is not None else carregar()   # lista compartilhada entre as docks
        self.items = []
        self.temporarios = []   # apps abertos que nao estao fixados na dock
        self.janelas = []       # todas as janelas abertas (atualizado a cada poucos ms)
        self.pulos = {}         # chave -> instante em que o app foi aberto (pulinho do icone)
        self.hover = -1
        self.press_idx = -1     # icone onde o botao foi apertado
        self.press_x = 0.0
        self.arrastando = -1    # icone que esta sendo arrastado
        self.drag_x = 0.0
        self._cand = -1
        self._t_cand = 0.0
        self._t_saida = None
        self.tela_cheia = False   # a janela da frente pede a dock escondida?
        self.revelada = False     # dock chamada pela borda de baixo / atalho
        self.oculto = 0.0         # 0 = totalmente visivel, 1 = totalmente escondida
        self.y_vis = 0
        self.menu_aberto = False
        self._t_fs = 0.0
        self._t_borda = None
        self._t_fora = None
        self._manter_ate = 0.0
        self.hora_txt = QTime.currentTime().toString("HH:mm")
        self.bateria = None       # (porcentagem, na_tomada) ou None
        self._t_bat = -100.0
        self.lixeira_cheia = False
        self._t_lixo = -100.0
        self.vidro = None
        self._sobe = 0.0            # quanto o icone mais alto passa do topo da barra (area de hover)
        self.pill_x = 0             # posicao/largura atuais da barra (ela se alarga com o zoom)
        self.pill_w = 0
        self.px0 = SIDE             # borda esquerda da barra em repouso
        self.win_w = 0
        self.bordas = []            # limites esquerdo/direito de cada icone no layout atual
        self._t_ult = time.monotonic()
        self._etq_idx = -1          # icone da etiqueta (continua aparecendo enquanto some)
        self._etq_a = 0.0           # opacidade da etiqueta (0 a 1)
        self._geo_vidro = (0, 0)
        self._vidro_vis = False
        self.painel_prefs = None
        self._t_tema = 0.0
        self._tema_nome = nome_tema()
        # atualizacoes (so a dock mestre usa)
        self._upd_res = None        # resultado da verificacao (preenchido pela thread)
        self._upd_busy = False
        self._upd_manual = False
        self._upd_nova = None       # ultima versao nova encontrada
        self._dl_res = None
        self._dl_busy = False
        self._upd_prox = time.monotonic() + 45
        self._montar_local()
        self.previa = Previa(self)
        self.pilha = Pilha(self)
        self.vidro = Vidro()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

        self.timer_barra = None
        self.timer_janelas = None
        if mestre is None:              # so a dock mestre cuida da barra do Windows e das janelas abertas
            self.timer_barra = QTimer(self)
            self.timer_barra.timeout.connect(manter_barra)
            self.timer_barra.start(40)

            self.timer_janelas = QTimer(self)
            self.timer_janelas.timeout.connect(self.atualizar_janelas)
            self.timer_janelas.start(INTERVALO_JANELAS)
            self.atualizar_janelas()

    # ---------- atualizacoes ----------
    def verificar_atualizacao(self, manual=False):
        """Pergunta ao GitHub se existe versao nova (em segundo plano, a tela nao trava)."""
        r = self.raiz()
        if atualizador is None or not GITHUB_REPO:
            if manual:
                QMessageBox.information(None, "Dock", "As atualizações ainda não foram configuradas "
                                        "(GITHUB_REPO vazio no dock.py).")
            return
        if r._upd_busy:
            return
        r._upd_busy = True
        r._upd_manual = manual

        def trabalho():
            try:
                res = atualizador.verificar(GITHUB_REPO, VERSAO)
            except Exception as e:
                res = {"status": "erro", "msg": str(e)}
            r._upd_res = res
        threading.Thread(target=trabalho, daemon=True).start()

    def _atualizacoes_tick(self, agora):
        if self.mestre is not None:
            return
        if self._upd_res is not None:
            res, self._upd_res = self._upd_res, None
            self._upd_busy = False
            self._tratar_verificacao(res)
        if self._dl_res is not None:
            res, self._dl_res = self._dl_res, None
            self._dl_busy = False
            self._tratar_download(res)
        if agora >= self._upd_prox:
            self._upd_prox = agora + 6 * 3600          # confere de novo a cada 6 horas
            if PREFS["atualizar_auto"] and GITHUB_REPO:
                self.verificar_atualizacao()

    def _tratar_verificacao(self, res):
        manual, self._upd_manual = self._upd_manual, False
        st = res.get("status")
        if st == "nova":
            self._upd_nova = res
            if manual:
                notas = res.get("notas") or ""
                resp = QMessageBox.question(None, "Dock", f"Existe uma versão nova: {res['versao']} "
                                            f"(você está na {VERSAO}).\n\n{notas}\n\nInstalar agora? "
                                            "A dock fecha e abre sozinha em alguns segundos.")
                if resp == QMessageBox.StandardButton.Yes:
                    self.baixar_e_instalar(manual=True)
            elif PREFS["atualizar_auto"]:
                self.baixar_e_instalar()
        elif manual:
            if st == "atual":
                QMessageBox.information(None, "Dock", f"Você já está na versão mais recente ({VERSAO}).")
            else:
                QMessageBox.warning(None, "Dock", res.get("msg", "Não consegui verificar as atualizações."))
        elif st == "erro":
            self._log_prefs("atualizacao: " + str(res.get("msg")))

    def baixar_e_instalar(self, manual=False):
        res = self._upd_nova
        if not res or self._dl_busy:
            return
        if not FROZEN:
            if manual:
                QMessageBox.information(None, "Dock", "A atualização automática só funciona na dock instalada "
                                        "(Dock-Setup.exe). Rodando pelo dock.py, atualize os arquivos você mesmo.")
            return
        self._dl_busy = True
        destino = os.path.join(tempfile.gettempdir(), "DockUpdate", f"Dock-Setup-{res['versao']}.exe")

        def trabalho():
            try:
                atualizador.baixar(res["url"], destino, res.get("sha_url"))
                self._dl_res = {"ok": True, "arq": destino, "versao": res["versao"]}
            except Exception as e:
                self._dl_res = {"ok": False, "msg": str(e)}
        threading.Thread(target=trabalho, daemon=True).start()

    def _tratar_download(self, res):
        if not res.get("ok"):
            self._log_prefs("atualizacao: download falhou: " + str(res.get("msg")))
            return
        try:
            # instalador silencioso: fecha esta dock, troca os arquivos e abre a nova versao
            subprocess.Popen([res["arq"], "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                             creationflags=0x00000008 | 0x00000200, close_fds=True)   # DETACHED | NEW_PROCESS_GROUP
            QApplication.quit()
        except Exception as e:
            self._log_prefs("atualizacao: nao consegui abrir o instalador: " + str(e))

    # ---------- varios monitores ----------
    def raiz(self):
        return self.mestre or self

    def todas(self):
        r = self.raiz()
        return [r] + r.irmas

    def copiar_estado_para(self, o):
        o.janelas = self.janelas
        o.temporarios = [dict(t) for t in self.temporarios]
        o.lixeira_cheia = self.lixeira_cheia

    def atualizar_telas(self):
        """Cria/remove as docks dos outros monitores (so na dock mestre)."""
        if self.mestre is not None:
            return
        telas = []
        if PREFS["todas_telas"]:
            telas = [s for s in QGuiApplication.screens() if s is not self.tela]
        for o in list(self.irmas):
            if o.tela not in telas:
                self.irmas.remove(o)
                o.encerrar()
        existentes = [o.tela for o in self.irmas]
        for s in telas:
            if s in existentes:
                continue
            o = Dock(tela=s, mestre=self)
            self.copiar_estado_para(o)
            self.irmas.append(o)
            o._montar_local()
            o.show()
            o.atualizar_vidro_visivel()
            o.raise_()

    def encerrar(self):
        try:
            self.timer.stop()
            self.previa.esconder()
            self.pilha.hide()
            if self.vidro is not None:
                self.vidro.hide()
                self.vidro.close()
            self.close()
            self.deleteLater()
        except Exception:
            pass

    # ---------- montagem ----------
    @property
    def n_fixos(self):
        return len(self.dados)

    @property
    def n_sistema(self):
        return sum(1 for it in self.items if it["tipo"] in TIPOS_SISTEMA)

    def faixa_arrasto(self, tipo):
        """(primeira, ultima) posicao em que um icone desse tipo pode ser solto."""
        if tipo == "fixo":
            return 1, self.n_fixos
        idxs = [i for i, it in enumerate(self.items) if it["tipo"] in TIPOS_SISTEMA]
        return (idxs[0], idxs[-1]) if idxs else (-1, -1)

    def tela_util(self):
        s = self.tela
        return s.geometry() if (_barra_ativa and self.mestre is None) else s.availableGeometry()

    def montar(self):
        """Reconstroi a dock deste e de todos os monitores (apps fixados, ordem, preferencias...)."""
        r = self.raiz()
        r._montar_local()
        for o in r.irmas:
            r.copiar_estado_para(o)
            o._montar_local()

    def _montar_local(self):
        esq_antiga = self.x()
        antigos = {it["chave"]: (it["size"], it["x"] + esq_antiga) for it in self.items}
        anterior = {it["chave"]: it for it in self.items if it["tipo"] == "fixo"}

        def novo(tipo, chave, caminho, nome, pix):
            return {"tipo": tipo, "chave": chave, "caminho": caminho, "nome": nome,
                    "pix": pix, "size": float(BASE), "v": 0.0, "x": 0.0, "lx": 0.0, "dx": 0.0,
                    "pulo": 0.0,
                    "janelas": [], "exes": set(), "nomes": set(), "pasta": None}

        self.items = [novo("inicio", CHAVE_INICIO, "", "Iniciar", icone_windows())]
        for d in self.dados:
            caminho = d.get("caminho", "")
            nome = d.get("nome") or os.path.splitext(os.path.basename(caminho))[0] or caminho
            it = novo("fixo", caminho, caminho, nome, pix_item(d))
            ant = anterior.get(caminho)
            if ant is not None:
                it["exes"], it["nomes"], it["pasta"] = ant["exes"], ant["nomes"], ant["pasta"]
            else:
                it["exes"], it["nomes"] = chaves_de(d)
                it["pasta"] = pasta_do_item(d)
            self.items.append(it)
        for t in self.temporarios:
            self.items.append(novo("temp", t["chave"], t["caminho"], t["nome"], pix_para(t["caminho"])))
        # itens de sistema, na ordem escolhida pelo usuario (arrastando) e salva nas preferencias
        sis = {
            "prefs": (PREFS["engrenagem"], "::prefs", "Preferências", icone_engrenagem()),
            "lixeira": (PREFS["lixeira"], "::lixeira", "Lixeira", icone_lixeira(self.lixeira_cheia)),
            "painel": (PREFS["painel"], "::painel", "Painel rápido", None),
            "relogio": (PREFS["relogio"], "::relogio", "Relógio", None),
        }
        ordem = [k for k in PREFS.get("ordem_sistema", []) if k in sis]
        ordem += [k for k in TIPOS_SISTEMA if k not in ordem]
        for k in ordem:
            ativo, chave, nome, pix = sis[k]
            if ativo:
                self.items.append(novo(k, chave, "", nome, pix))

        n = len(self.items)
        base_w = PAD_X * 2 + SLOT * n
        # a janela e larga o bastante para a barra se alargar quando o zoom empurra os icones
        folga = int((ZOOM - BASE) * (ALCANCE_ZOOM + 1.0) / 2) + 6
        self.px0 = SIDE + folga
        self.win_w = int(base_w + 2 * self.px0)
        self.resize(self.win_w, TIP_H + PILL_H)

        screen = self.tela_util()
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.bottom() - self.height() - MARGEM
        self.y_vis = y
        self.move(x, self.y_atual())

        for it in self.items:
            if it["chave"] in antigos:
                it["size"] = float(antigos[it["chave"]][0])
        self.recalcular_layout()
        for it in self.items:
            if it["chave"] in antigos:
                it["dx"] = (antigos[it["chave"]][1] - x) - it["lx"]    # desliza da posicao antiga para a nova
            else:
                it["dx"] = 0.0
            it["x"] = it["lx"] + it["dx"]
        self.distribuir_janelas()
        self.sincronizar_vidro()
        self.update()

    def recalcular_layout(self):
        """Posiciona os icones lado a lado usando o tamanho ATUAL de cada um: quem cresce empurra os vizinhos
        e a barra se alarga para os dois lados (ela continua centralizada), como na dock do macOS."""
        gap = SLOT - BASE
        total = 0.0
        for it in self.items:
            total += max(1.0, it["size"]) + gap
        largura = PAD_X * 2 + total
        esq = (self.win_w - largura) / 2.0
        x = esq + PAD_X
        self.bordas = [x]
        for it in self.items:
            w = max(1.0, it["size"]) + gap
            it["lx"] = x + w / 2.0
            x += w
            self.bordas.append(x)
        self.pill_w = int(round(largura))
        self.pill_x = int(round(esq))

    def centro(self, i):
        """Centro do icone i com a dock em repouso (sem zoom)."""
        return self.px0 + PAD_X + SLOT * (i + 0.5)

    def x_em_repouso(self, x):
        """Converte a posicao do mouse (layout com zoom) para o layout em repouso. Assim o icone que esta
        debaixo do mouse continua sendo o que mais cresce, mesmo com os vizinhos empurrados."""
        b, n = self.bordas, len(self.items)
        if n == 0 or len(b) < n + 1:
            return x
        if x <= b[0]:
            return self.px0 + PAD_X + (x - b[0])
        if x >= b[-1]:
            return self.px0 + PAD_X + SLOT * n + (x - b[-1])
        i = min(bisect.bisect_right(b, x) - 1, n - 1)
        frac = (x - b[i]) / max(1e-6, b[i + 1] - b[i])
        return self.px0 + PAD_X + SLOT * (i + frac)

    def zona(self):
        s = self._sobe
        return QRectF(self.pill_x, TIP_H - s, self.pill_w, PILL_H + s).adjusted(0, -12, 0, 10)

    def indice_em(self, pos):
        if not self.zona().contains(QPointF(pos)):
            return -1
        b = self.bordas
        if len(b) < 2 or pos.x() < b[0] or pos.x() >= b[-1]:
            return -1
        return min(bisect.bisect_right(b, pos.x()) - 1, len(self.items) - 1)

    # ---------- vidro ----------
    def moveEvent(self, event):
        super().moveEvent(event)
        self.sincronizar_vidro()

    def sincronizar_vidro(self):
        if self.vidro is None:
            return
        self.vidro.ajustar(self.x() + self.pill_x, self.y() + TIP_H, int(self.pill_w), PILL_H)

    def atualizar_vidro_visivel(self):
        if self.vidro is None:
            return
        quer = vidro_com_desfoque() and self.oculto < 0.98
        if quer != self._vidro_vis:
            self._vidro_vis = quer
            if quer:
                self.sincronizar_vidro()
                self.vidro.show()
                self.raise_()
            else:
                self.vidro.hide()

    # ---------- apps abertos ----------
    def distribuir_janelas(self):
        for it in self.items:
            if it["tipo"] == "fixo":
                it["janelas"] = [j for j in self.janelas if janela_combina(j, it["exes"], it["nomes"])]
            elif it["tipo"] == "temp":
                it["janelas"] = [j for j in self.janelas if j["exe"] == it["chave"]]
            else:
                it["janelas"] = []

    def combina_algum_fixo(self, j):
        for it in self.items:
            if it["tipo"] == "fixo" and janela_combina(j, it["exes"], it["nomes"]):
                return True
        return False

    def atualizar_janelas(self):
        if self.mestre is not None:
            return self.mestre.atualizar_janelas()
        agora = time.monotonic()
        if agora - self._t_lixo > 3:
            self._t_lixo = agora
            self.atualizar_lixeira()
        if any(d.arrastando >= 0 or d.press_idx >= 0 for d in self.todas()):
            return
        try:
            abertas = listar_janelas()
        except Exception:
            return
        self.janelas = abertas

        por_exe = {}
        for j in abertas:
            if not PREFS["mostrar_abertos"] or self.combina_algum_fixo(j):
                continue
            if j["exe"] not in por_exe:
                por_exe[j["exe"]] = j["real"]

        antiga = [t["chave"] for t in self.temporarios]
        ordem = [e for e in antiga if e in por_exe] + [e for e in por_exe if e not in antiga]
        self.temporarios = [
            {"chave": e, "caminho": por_exe[e], "nome": nome_bonito(por_exe[e])} for e in ordem
        ]
        if ordem != antiga:
            self.montar()
        else:
            self.distribuir_janelas()
        self.sincronizar_previa()
        self.update()
        for o in self.irmas:                      # as docks dos outros monitores acompanham
            self.copiar_estado_para(o)
            if ordem == antiga:
                o.distribuir_janelas()
            o.sincronizar_previa()
            o.update()

    def atualizar_lixeira(self):
        if self.mestre is not None:
            return self.mestre.atualizar_lixeira()
        cheia = lixeira_tem_itens()
        if cheia != self.lixeira_cheia:
            self.lixeira_cheia = cheia
            for d in self.todas():
                d.lixeira_cheia = cheia
                for it in d.items:
                    if it["tipo"] == "lixeira":
                        it["pix"] = icone_lixeira(cheia)
                d.update()

    def sincronizar_previa(self):
        if not self.previa.isVisible():
            return
        for it in self.items:
            if it["chave"] == self.previa.chave:
                if it["janelas"]:
                    self.previa.atualizar(it["janelas"])
                else:
                    self.previa.esconder()
                return
        self.previa.esconder()

    # ---------- previa ao passar o mouse ----------
    def mostrar_previa(self, idx):
        it = self.items[idx]
        self.previa.configurar(it, self.x() + it["x"], self.y_vis + TIP_H - 14 - max(0, ZOOM - BASE - 4), self.tela_util())

    def atualizar_previa(self, dentro, agora):
        alvo = -1
        if (PREFS["previa"] and self.arrastando < 0 and self.oculto < 0.05
                and 0 <= self.hover < len(self.items) and self.items[self.hover]["janelas"]):
            alvo = self.hover
        visivel = self.previa.isVisible()

        if alvo >= 0:
            self._t_saida = None
            if visivel:
                self.mostrar_previa(alvo)
            elif self._cand != alvo:
                self._cand = alvo
                self._t_cand = agora
            elif agora - self._t_cand >= ATRASO_PREVIA:
                self.mostrar_previa(alvo)
        else:
            self._cand = -1
            if visivel:
                sobre = self.previa.frameGeometry().contains(QCursor.pos())
                if sobre:
                    self._t_saida = None
                elif self._t_saida is None:
                    self._t_saida = agora
                elif agora - self._t_saida >= GRACA_PREVIA:
                    self.previa.esconder()
                    self._t_saida = None

    # ---------- esconder em tela cheia / maximizada ----------
    def y_atual(self):
        geom = self.tela.geometry()
        y_escondida = geom.bottom() + 1
        return int(round(self.y_vis + (y_escondida - self.y_vis) * self.oculto))

    def area_dock(self):
        geom = self.tela.geometry()
        topo = self.y_vis + TIP_H - 20
        return QRect(self.x() + self.pill_x - 20, topo, int(self.pill_w) + 40, geom.bottom() + 1 - topo + 1)

    def alternar_por_atalho(self):
        """Ctrl+Alt+D: mostra a dock escondida por alguns segundos (ou esconde de novo), em todos os monitores."""
        agora = time.monotonic()
        todas = self.todas()
        revelar = not all(d.revelada for d in todas)
        for d in todas:
            if revelar:
                d.revelada = True
                d._manter_ate = agora + 5.0
                d._t_fora = None
            else:
                d.revelada = False
                d._manter_ate = 0.0

    def retangulo_fisico_barra(self):
        """(esq, topo, dir, base) da barra em pixels reais da tela (funciona em qualquer monitor)."""
        r = wintypes.RECT()
        if not user32.GetWindowRect(int(self.winId()), ctypes.byref(r)):
            return (0, 0, 0, 0)
        dpr = self.devicePixelRatioF()
        esq = r.left + int(self.pill_x * dpr)
        topo = r.top + int(TIP_H * dpr)
        return (esq, topo, esq + int(self.pill_w * dpr), topo + int(PILL_H * dpr))

    def atualizar_autohide(self, agora):
        if agora - self._t_fs > 0.15:
            self._t_fs = agora
            if AUTO_OCULTAR and OCULTAR_QUANDO != "nunca":
                self.tela_cheia = deve_esconder_dock(self.retangulo_fisico_barra(), int(self.winId()))
            else:
                self.tela_cheia = False

        if not self.tela_cheia:
            self.revelada = False
            self._t_borda = self._t_fora = None
            alvo = 0.0
        else:
            pos = QCursor.pos()
            geom = self.tela.geometry()
            if self.revelada:
                sobre_previa = self.previa.isVisible() and self.previa.frameGeometry().contains(pos)
                if (self.area_dock().contains(pos) or sobre_previa or self.menu_aberto
                        or self.arrastando >= 0 or agora < self._manter_ate or self.pilha.isVisible()):
                    self._t_fora = None
                elif self._t_fora is None:
                    self._t_fora = agora
                elif agora - self._t_fora >= ATRASO_SUMIR:
                    self.revelada = False
                    self._t_fora = None
            else:
                na_borda = (pos.y() >= geom.bottom() - 1 and geom.left() <= pos.x() <= geom.right())
                if na_borda:
                    if self._t_borda is None:
                        self._t_borda = agora
                    elif agora - self._t_borda >= ATRASO_BORDA:
                        self.revelada = True
                        self._t_borda = None
                        self._t_fora = None
                else:
                    self._t_borda = None
            alvo = 0.0 if self.revelada else 1.0

        if alvo > self.oculto and self.previa.isVisible():
            self.previa.esconder()
        novo = self.oculto + (alvo - self.oculto) * 0.22
        if abs(alvo - novo) < 0.003:
            novo = alvo
        if novo != self.oculto:
            self.oculto = novo
        ny = self.y_atual()
        if ny != self.y():
            self.move(self.x(), ny)
        self.atualizar_vidro_visivel()

    # ---------- animacao ----------
    def tick(self):
        local = self.mapFromGlobal(QCursor.pos())
        dentro = self.zona().contains(QPointF(local))
        agora = time.monotonic()
        self.atualizar_autohide(agora)

        self._atualizacoes_tick(agora)
        mudou = False
        dt = min(0.05, max(0.001, agora - self._t_ult))
        self._t_ult = agora
        dt *= PREFS["animacao_pct"] / 100.0
        passos = max(1, int(math.ceil(dt / 0.008)))      # sub-passos mantem a mola estavel
        h = dt / passos
        mx = self.x_em_repouso(local.x()) if dentro else 0.0

        # 1) tamanho de cada icone: curva de sino em volta do mouse + mola (um leve "balanco" no final)
        for i, it in enumerate(self.items):
            if self.arrastando >= 0:
                alvo = float(ZOOM) if i == self.arrastando else float(BASE)
            elif dentro:
                d = abs(mx - self.centro(i))
                t = 0.0 if d >= RANGE else 0.5 * (1.0 + math.cos(math.pi * d / RANGE))
                alvo = BASE + (ZOOM - BASE) * t
            else:
                alvo = float(BASE)
            s, v = it["size"], it["v"]
            for _ in range(passos):
                v += ((alvo - s) * MOLA_K - v * MOLA_C) * h
                s += v * h
            if abs(alvo - s) < 0.02 and abs(v) < 0.05:
                s, v = alvo, 0.0
            else:
                mudou = True
            it["size"], it["v"] = s, v

        # 2) posicao: quem cresce empurra os vizinhos e a barra se alarga (sempre centralizada)
        antes = (self.pill_x, self.pill_w)
        self.recalcular_layout()
        if (self.pill_x, self.pill_w) != antes:
            self.sincronizar_vidro()
            mudou = True
        for i, it in enumerate(self.items):
            if abs(it["dx"]) > 0.05:                      # deslize ao adicionar / tirar / reordenar
                it["dx"] *= 1.0 - vel_anim(0.35)
                mudou = True
            else:
                it["dx"] = 0.0
            novo_x = self.drag_x if i == self.arrastando else it["lx"] + it["dx"]
            if abs(novo_x - it["x"]) > 0.05:
                mudou = True
            it["x"] = novo_x

            # pulinho enquanto o app esta abrindo
            off = 0.0
            t0 = self.pulos.get(it["chave"])
            if t0 is not None:
                dt = agora - t0
                if dt > 12 or (it["janelas"] and dt > 1.2):
                    del self.pulos[it["chave"]]
                else:
                    off = abs(math.sin(dt * math.pi / 0.6)) * 12
            if abs(off - it["pulo"]) > 0.01:
                mudou = True
            it["pulo"] = off

        # a barra de vidro tem altura fixa; so os icones sobem acima dela. Guardamos o quanto passam do
        # topo para a area de hover acompanhar os icones ampliados.
        sobe = 0.0
        for i, it in enumerate(self.items):
            s_ = it["size"] + it["pulo"] + (6 if i == self.arrastando else 0) - BASE - PAD_Y
            if s_ > sobe:
                sobe = s_
        self._sobe = max(0.0, sobe)

        if self.arrastando >= 0:
            novo_hover = -1
        else:
            novo_hover = self.indice_em(local) if dentro else -1
        if novo_hover != self.hover:
            self.hover = novo_hover
            mudou = True

        # etiqueta: aparece/some suavemente e segue o topo do icone
        if self.hover >= 0:
            self._etq_idx = self.hover
            alvo_a = 1.0
        else:
            alvo_a = 0.0
        na = self._etq_a + (alvo_a - self._etq_a) * 0.35
        if abs(na - alvo_a) < 0.02:
            na = alvo_a
            if na == 0.0:
                self._etq_idx = -1
        if na != self._etq_a:
            self._etq_a = na
            mudou = True

        txt = QTime.currentTime().toString(self.formato_hora())
        if txt != self.hora_txt:
            self.hora_txt = txt
            mudou = True
        if PREFS["tema"] == "auto" and agora - self._t_tema > 3:
            self._t_tema = agora
            novo_tema = nome_tema()
            if novo_tema != self._tema_nome:
                self._tema_nome = novo_tema
                self.aplicar_visual()
                mudou = True
        if agora - self._t_bat > 10:
            self._t_bat = agora
            novo_bat = estado_bateria()
            if novo_bat != self.bateria:
                self.bateria = novo_bat
                mudou = True

        self.atualizar_previa(dentro, agora)

        if mudou:
            self.update()

    # ---------- adicionar / remover ----------
    def adicionar(self, caminho):
        caminho = os.path.normpath(caminho)
        for d in self.dados:
            if os.path.normcase(d.get("caminho", "")) == os.path.normcase(caminho):
                return False
        nome = os.path.splitext(os.path.basename(caminho))[0] or os.path.basename(caminho) or caminho
        self.dados.append({"caminho": caminho, "nome": nome})
        return True

    def escolher_arquivo(self):
        caminho, _ = QFileDialog.getOpenFileName(
            None,
            "Escolha um programa ou atalho",
            PROGRAMAS,
            "Programas e atalhos (*.lnk *.exe *.bat);;Todos os arquivos (*.*)",
        )
        if caminho and self.adicionar(caminho):
            salvar(self.dados)
            self.montar()
            self.atualizar_janelas()

    def escolher_pasta(self):
        caminho = QFileDialog.getExistingDirectory(None, "Escolha uma pasta")
        if caminho and self.adicionar(caminho):
            salvar(self.dados)
            self.montar()
            self.atualizar_janelas()

    def adicionar_app_store(self):
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            apps = listar_apps_store()
        except Exception as e:
            print(f"Erro ao listar apps da Store: {e}")
            apps = []
        finally:
            QGuiApplication.restoreOverrideCursor()
        if not apps:
            QMessageBox.information(None, "Apps da Microsoft Store",
                                    "Nao consegui listar os apps da Microsoft Store.")
            return
        apps.sort(key=lambda a: a["Nome"].lower())
        nomes = [a["Nome"] for a in apps]
        escolhido, ok = QInputDialog.getItem(None, "Apps da Microsoft Store",
                                             "Escolha o app para fixar na dock:", nomes, 0, False)
        if not ok:
            return
        app = apps[nomes.index(escolhido)]
        caminho = "shell:AppsFolder\\" + app["AUMID"]
        for d in self.dados:
            if d.get("caminho", "").lower() == caminho.lower():
                return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            icone, exe = dados_app_store(app)
        finally:
            QGuiApplication.restoreOverrideCursor()
        self.dados.append({"caminho": caminho, "nome": app["Nome"], "icone": icone, "exe": exe})
        salvar(self.dados)
        self.montar()
        self.atualizar_janelas()

    def mover(self, idx, delta):
        # idx e a posicao em self.dados
        novo = idx + delta
        if 0 <= novo < len(self.dados):
            self.dados[idx], self.dados[novo] = self.dados[novo], self.dados[idx]
            salvar(self.dados)
            self.montar()

    def remover(self, idx):
        # idx e a posicao em self.items
        del self.dados[idx - 1]
        salvar(self.dados)
        self.montar()
        self.atualizar_janelas()

    def manter_na_dock(self, idx):
        chave = self.items[idx]["chave"]
        if self.adicionar(self.items[idx]["caminho"]):
            salvar(self.dados)
            self.temporarios = [t for t in self.temporarios if t["chave"] != chave]
            self.montar()
            self.atualizar_janelas()

    def abrir_pilha(self, it):
        idx = self.items.index(it)
        self.previa.esconder()
        self.pilha.abrir(it["pasta"], it["nome"], self.x() + it["x"],
                         self.y_vis + TIP_H - 10 - max(0, ZOOM - BASE - 4), self.tela_util())

    # ---------- preferencias ----------
    def abrir_preferencias(self):
        # espera o menu do botao direito fechar de vez: se o painel abre com o menu ainda no ar,
        # o Windows devolve o foco para a janela de antes e o painel nao aparece
        QTimer.singleShot(120, self._abrir_preferencias_agora)

    def _log_prefs(self, texto):
        try:
            with open(os.path.join(DADOS, "prefs_debug.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + texto + "\n")
        except Exception:
            pass

    def _abrir_preferencias_agora(self):
        if self.mestre is not None:            # um unico painel, sempre da dock mestre
            return self.mestre._abrir_preferencias_agora()
        try:
            self._log_prefs("abrir_preferencias chamado")
            if self.painel_prefs is None:
                self.painel_prefs = PainelPreferencias(self)
                self._log_prefs("painel criado")
            self.painel_prefs.mostrar()
            pp = self.painel_prefs
            g = pp.geometry()
            self._log_prefs(f"painel visivel={pp.isVisible()} geom=({g.x()},{g.y()},{g.width()},{g.height()})")
            if not pp.isVisible():
                QMessageBox.warning(None, "Dock", "O painel de Preferências foi criado mas não ficou visível.\n"
                                    "Veja prefs_debug.log na pasta da dock.")
        except Exception:
            self.painel_prefs = None
            import traceback
            texto = traceback.format_exc()
            _registrar_erro(*sys.exc_info())
            QMessageBox.warning(None, "Dock - erro ao abrir as Preferências",
                                texto[-1200:] + "\n\nO erro também foi salvo em:\n" + ARQ_ERRO)

    def aplicar_visual(self):
        """Reaplica tema, vidro e cantos depois de uma mudanca."""
        if self.vidro is not None:
            self.vidro.atualizar_estilo()
        self.atualizar_vidro_visivel()
        if self.painel_prefs is not None:
            self.painel_prefs.restilizar()
        self.update()

    def definir_pref(self, chave, valor, persistir=True):
        PREFS[chave] = valor
        if persistir:
            salvar_prefs()
        aplicar_prefs()
        if chave == "ocultar_barra":
            if valor:
                esconder_barra()
            else:
                restaurar_barra()
        self._reaplicar_em_todas(chave == "todas_telas")

    def recarregar_prefs(self):
        """Depois de restaurar os padroes: reaplica tudo."""
        aplicar_prefs()
        if PREFS["ocultar_barra"]:
            esconder_barra()
        else:
            restaurar_barra()
        self._reaplicar_em_todas(True)

    def _reaplicar_em_todas(self, refazer_telas):
        """Aplica a mudanca de preferencia na dock de cada monitor."""
        r = self.raiz()
        for d in r.todas():
            d._tema_nome = nome_tema()
            d.previa.esconder()
        r.montar()
        if refazer_telas:
            r.atualizar_telas()
        for d in r.todas():
            d.aplicar_visual()

    def formato_hora(self):
        return "HH:mm" if PREFS["relogio_24h"] else "h:mm AP"

    # ---------- eventos do mouse ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            self.press_idx = self.indice_em(pos)
            self.press_x = float(pos.x())
            self.arrastando = -1

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self.press_idx < 0 or not self.items:
            return
        ref = self.arrastando if self.arrastando >= 0 else self.press_idx
        if ref >= len(self.items):
            return
        tipo = self.items[ref]["tipo"]
        if tipo != "fixo" and tipo not in TIPOS_SISTEMA:
            return   # apps fixados e itens de sistema podem ser arrastados (cada grupo dentro do seu espaco)
        lo, hi = self.faixa_arrasto(tipo)
        if lo < 0 or hi < lo:
            return
        x = event.position().x()
        if self.arrastando < 0:
            if abs(x - self.press_x) < LIMIAR_ARRASTO:
                return
            self.arrastando = self.press_idx
            self.previa.esconder()

        self.drag_x = max(self.centro(lo), min(self.centro(hi), x))
        alvo = int((self.drag_x - self.px0 - PAD_X) // SLOT)
        alvo = max(lo, min(hi, alvo))
        if alvo != self.arrastando:
            if tipo == "fixo":
                self.dados.insert(alvo - 1, self.dados.pop(self.arrastando - 1))
            self.items.insert(alvo, self.items.pop(self.arrastando))
            self.arrastando = alvo
            if tipo != "fixo":
                PREFS["ordem_sistema"] = [it["tipo"] for it in self.items if it["tipo"] in TIPOS_SISTEMA]
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.arrastando >= 0:
            tipo = self.items[self.arrastando]["tipo"] if self.arrastando < len(self.items) else "fixo"
            self.arrastando = -1
            if tipo == "fixo":
                salvar(self.dados)
            else:
                salvar_prefs()
            self.montar()          # as docks dos outros monitores pegam a nova ordem
        elif self.press_idx >= 0:
            idx = self.indice_em(event.position().toPoint())
            if idx == self.press_idx and 0 <= idx < len(self.items):
                self.clicar(self.items[idx])
        self.press_idx = -1
        self.update()

    def clicar(self, it):
        self.previa.esconder()
        tipo = it["tipo"]
        if tipo == "inicio":
            abrir_menu_iniciar()
            return
        if tipo == "prefs":
            self.abrir_preferencias()
            return
        if tipo == "painel":
            abrir_painel_rapido()
            return
        if tipo == "relogio":
            abrir_central_notificacoes()
            return
        if tipo == "lixeira":
            try:
                os.startfile("shell:RecycleBinFolder")
            except Exception as e:
                print(f"Erro ao abrir a Lixeira: {e}")
            return
        if tipo == "fixo" and it["pasta"]:
            self.abrir_pilha(it)
            return
        # confere de novo o que esta aberto (evita abrir uma segunda copia sem querer)
        try:
            self.janelas = listar_janelas()
            self.distribuir_janelas()
        except Exception:
            pass
        if it["janelas"]:
            ativar_janelas(it["janelas"])
        elif tipo == "fixo":
            try:
                os.startfile(it["caminho"])
                if PREFS["pulinho"]:
                    self.pulos[it["chave"]] = time.monotonic()
            except Exception as e:
                print(f"Erro ao abrir {it['caminho']}: {e}")
        else:
            self.atualizar_janelas()

    # ---------- menu do botao direito ----------
    def contextMenuEvent(self, event):
        self.menu_aberto = True
        try:
            self._menu_contexto(event)
        finally:
            self.menu_aberto = False

    def _menu_contexto(self, event):
        self.previa.esconder()
        idx = self.indice_em(event.pos())
        it = self.items[idx] if idx >= 0 else None
        tipo = it["tipo"] if it else None
        menu = QMenu(self)
        acoes = {}

        def add(m, texto, fn, marcado=None):
            a = m.addAction(texto)
            if marcado is not None:
                a.setCheckable(True)
                a.setChecked(bool(marcado))
            acoes[a] = fn
            return a

        if tipo in ("fixo", "temp"):
            fm = QFontMetrics(menu.font())
            for j in it["janelas"][:8]:
                titulo = j["titulo"] or it["nome"]
                add(menu, fm.elidedText(titulo, Qt.ElideRight, 320), lambda h=j["hwnd"]: trazer_janela(h))
            if it["janelas"]:
                menu.addSeparator()
                add(menu, "Fechar todas as janelas",
                    lambda js=list(it["janelas"]): [fechar_janela(j["hwnd"]) for j in js])
                menu.addSeparator()
            if not it["pasta"]:
                add(menu, "Abrir ao iniciar o Windows",
                    lambda c=it["caminho"], n=it["nome"]: alternar_inicio(c, n),
                    marcado=inicia_com_windows(it["nome"]))
                menu.addSeparator()

        if tipo == "fixo":
            if it["pasta"]:
                add(menu, "Abrir pasta no Explorer", lambda p=it["pasta"]: os.startfile(p))
            add(menu, "Remover da dock", lambda i=idx: self.remover(i))
            if idx > 1:
                add(menu, "Mover para a esquerda", lambda i=idx: self.mover(i - 1, -1))
            if idx < self.n_fixos:
                add(menu, "Mover para a direita", lambda i=idx: self.mover(i - 1, 1))
            menu.addSeparator()
        elif tipo == "temp":
            add(menu, "Manter na dock", lambda i=idx: self.manter_na_dock(i))
            menu.addSeparator()
        elif tipo == "lixeira":
            add(menu, "Abrir Lixeira", lambda: os.startfile("shell:RecycleBinFolder"))
            add(menu, "Esvaziar Lixeira", lambda: (esvaziar_lixeira(), self.atualizar_lixeira()))
            menu.addSeparator()
        elif tipo == "relogio":
            add(menu, "Configurações de data e hora", lambda: os.startfile("ms-settings:dateandtime"))
            menu.addSeparator()
        elif tipo == "painel":
            add(menu, "Configurações de som", lambda: os.startfile("ms-settings:sound"))
            add(menu, "Configurações de Wi-Fi", lambda: os.startfile("ms-settings:network-wifi"))
            add(menu, "Configurações de bateria", lambda: os.startfile("ms-settings:batterysaver"))
            menu.addSeparator()

        add(menu, "Adicionar programa...", self.escolher_arquivo)
        add(menu, "Adicionar pasta...", self.escolher_pasta)
        add(menu, "Adicionar app da Microsoft Store...", self.adicionar_app_store)
        menu.addSeparator()

        add(menu, "Preferências...", self.abrir_preferencias)

        menu.addSeparator()
        add(menu, "Fechar a dock", QApplication.quit)

        escolhida = menu.exec(event.globalPos())
        if escolhida is not None:
            fn = acoes.get(escolhida)
            if fn:
                fn()

    # ---------- arrastar arquivos para dentro da dock ----------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        idx = self.indice_em(event.position().toPoint())
        if idx >= 0 and self.items[idx]["tipo"] == "lixeira":
            mover_para_lixeira(urls)
            self.atualizar_lixeira()
            event.acceptProposedAction()
            return
        mudou = False
        for caminho in urls:
            if self.adicionar(caminho):
                mudou = True
        if mudou:
            salvar(self.dados)
            self.montar()
            self.atualizar_janelas()
        event.acceptProposedAction()

    # ---------- desenho ----------
    def texto_etiqueta(self, it):
        tipo = it["tipo"]
        if tipo == "relogio":
            return QDate.currentDate().toString("dddd, d 'de' MMMM")
        if tipo == "painel":
            if self.bateria:
                return f"Painel rápido  ·  bateria {self.bateria[0]}%"
            return "Painel rápido"
        if e_spotify(it["janelas"]):
            titulo = it["janelas"][0]["titulo"]
            if titulo and not titulo.lower().startswith("spotify"):
                return titulo
        return it["nome"]

    def desenhar_relogio(self, p, alvo):
        T = tema_atual()
        s = alvo.width()
        p.setPen(Qt.NoPen)
        p.setBrush(T["chip"])
        p.drawRoundedRect(alvo, s * 0.24, s * 0.24)
        data = PREFS["relogio_data"]
        f = QFont("Segoe UI", 1)
        f.setPixelSize(max(8, int(s * (0.30 if len(self.hora_txt) <= 5 else 0.21))))
        f.setBold(True)
        p.setFont(f)
        p.setPen(com_alpha(T["texto"], 245))
        topo = alvo.top() + (s * 0.12 if data else 0)
        alt = s * 0.42 if data else s
        p.drawText(QRectF(alvo.left(), topo, s, alt), Qt.AlignCenter, self.hora_txt)
        if data:
            f2 = QFont("Segoe UI", 1)
            f2.setPixelSize(max(7, int(s * 0.19)))
            p.setFont(f2)
            p.setPen(T["texto2"])
            p.drawText(QRectF(alvo.left(), alvo.top() + s * 0.52, s, s * 0.32), Qt.AlignCenter,
                       QDate.currentDate().toString("dd/MM"))

    def desenhar_painel(self, p, alvo):
        T = tema_atual()
        s = alvo.width()
        l, t = alvo.left(), alvo.top()
        p.setPen(Qt.NoPen)
        p.setBrush(T["chip"])
        p.drawRoundedRect(alvo, s * 0.24, s * 0.24)
        cor_i = com_alpha(T["texto"], 235)
        espessura = max(1.5, s * 0.045)
        dy = 0.0 if self.bateria else s * 0.10

        # Wi-Fi
        p.setPen(QPen(cor_i, espessura, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        bx, by = l + s * 0.30, t + s * 0.50 + dy
        for r in (0.09, 0.16, 0.23):
            rr = s * r
            p.drawArc(QRectF(bx - rr, by - rr, rr * 2, rr * 2), 45 * 16, 90 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(cor_i)
        p.drawEllipse(QPointF(bx, by), s * 0.03, s * 0.03)

        # alto-falante
        x0, ym = l + s * 0.58, t + s * 0.36 + dy
        p.drawPolygon(QPolygonF([
            QPointF(x0, ym - s * 0.05), QPointF(x0 + s * 0.07, ym - s * 0.05),
            QPointF(x0 + s * 0.15, ym - s * 0.12), QPointF(x0 + s * 0.15, ym + s * 0.12),
            QPointF(x0 + s * 0.07, ym + s * 0.05), QPointF(x0, ym + s * 0.05)]))
        p.setPen(QPen(cor_i, espessura, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(x0 + s * 0.10, ym - s * 0.10, s * 0.20, s * 0.20), -45 * 16, 90 * 16)

        # bateria
        if self.bateria:
            pct, tomada = self.bateria
            corpo = QRectF(l + s * 0.12, t + s * 0.66, s * 0.40, s * 0.20)
            p.setPen(QPen(cor_i, max(1.2, s * 0.03)))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(corpo, s * 0.04, s * 0.04)
            p.setPen(Qt.NoPen)
            p.setBrush(cor_i)
            p.drawRect(QRectF(corpo.right() + s * 0.01, corpo.top() + s * 0.06, s * 0.03, s * 0.08))
            nivel = max(0.0, min(1.0, pct / 100.0))
            p.setBrush(T["bateria_ok"] if (tomada or pct > 20) else T["bateria_baixa"])
            p.drawRoundedRect(corpo.adjusted(s * 0.02, s * 0.02,
                                             -s * 0.02 - (1 - nivel) * (corpo.width() - s * 0.04), -s * 0.02),
                              s * 0.02, s * 0.02)
            f = QFont("Segoe UI", 1)
            f.setPixelSize(max(7, int(s * 0.16)))
            p.setFont(f)
            p.setPen(cor_i)
            p.drawText(QRectF(l + s * 0.58, t + s * 0.62, s * 0.40, s * 0.28),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{pct}%")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

        T = tema_atual()
        pill = QRectF(self.pill_x, TIP_H, self.pill_w, PILL_H)
        p.setPen(QPen(cor_borda(), 1.5))
        if vidro_com_desfoque():
            # o fundo fica por conta da janela de vidro. Mas pixels 100% transparentes deixam o clique
            # atravessar a dock (o menu do botao direito viraria o do programa de tras); alpha 1 pega o clique.
            p.setBrush(QColor(0, 0, 0, 1))
        else:
            r_, g_, b_ = T["tint"]
            p.setBrush(QColor(r_, g_, b_, min(255, 20 + int(PREFS["vidro_opacidade"] * 2.0))))
        rr = raio_efetivo()
        p.drawRoundedRect(pill.adjusted(0.75, 0.75, -0.75, -0.75), rr, rr)

        # divisorias discretas: depois do Iniciar, antes dos apps abertos e antes dos itens do sistema
        n_temp = len(self.temporarios)
        divisores = [1]
        if n_temp:
            divisores.append(1 + self.n_fixos)
        if self.n_sistema:
            divisores.append(1 + self.n_fixos + n_temp)
        p.setPen(QPen(T["sep"], 1))
        for k in divisores:
            if k >= len(self.bordas):
                continue
            xd = self.bordas[k]
            p.drawLine(QPointF(xd, TIP_H + PAD_Y + 6), QPointF(xd, TIP_H + PILL_H - PAD_Y - 6))

        base_y = TIP_H + PILL_H - PAD_Y
        ordem = [i for i in range(len(self.items)) if i != self.arrastando]
        if 0 <= self.arrastando < len(self.items):
            ordem.append(self.arrastando)   # o icone arrastado fica por cima dos outros

        for i in ordem:
            it = self.items[i]
            s = it["size"]
            cx = it["x"]
            y_base = base_y - (6 if i == self.arrastando else 0) - it["pulo"]
            alvo = QRectF(cx - s / 2, y_base - s, s, s)
            apertado = (i == self.press_idx and self.arrastando < 0)
            p.setOpacity(0.65 if apertado else 1.0)
            if it["tipo"] == "relogio":
                self.desenhar_relogio(p, alvo)
            elif it["tipo"] == "painel":
                self.desenhar_painel(p, alvo)
            elif it["pix"] is not None:
                p.drawPixmap(alvo, it["pix"], QRectF(it["pix"].rect()))
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(90, 90, 105))
                p.drawRoundedRect(alvo, 12, 12)
                fonte = QFont("Segoe UI", 1)
                fonte.setPixelSize(max(8, int(s * 0.45)))
                fonte.setBold(True)
                p.setFont(fonte)
                p.setPen(QColor(255, 255, 255))
                p.drawText(alvo, Qt.AlignCenter, (it["nome"][:1] or "?").upper())
            p.setOpacity(1.0)

            if it["janelas"] and PREFS["bolinhas"]:   # bolinha de "app aberto"
                p.setPen(Qt.NoPen)
                p.setBrush(com_alpha(T["texto"], 215))
                p.drawEllipse(QPointF(cx, TIP_H + PILL_H - 6), 2.2, 2.2)

        if PREFS["etiquetas"] and 0 <= self._etq_idx < len(self.items) and self._etq_a > 0.02:
            it = self.items[self._etq_idx]
            if not (self.previa.isVisible() and self.previa.chave == it["chave"]):
                fonte = QFont("Segoe UI", 9)
                p.setFont(fonte)
                fm = QFontMetrics(fonte)
                texto = fm.elidedText(self.texto_etiqueta(it), Qt.ElideRight, 380)
                largura = fm.horizontalAdvance(texto) + 24
                altura = 26
                x = it["x"] - largura / 2
                x = max(2, min(x, self.width() - largura - 2))
                topo_icone = base_y - it["size"] - it["pulo"]       # a etiqueta fica logo acima do icone
                topo_icone = min(topo_icone, TIP_H + PAD_Y)
                y = max(1.0, topo_icone - 8 - altura + (1.0 - self._etq_a) * 6)
                caixa = QRectF(x, y, largura, altura)
                p.setOpacity(self._etq_a)
                p.setPen(QPen(T["etq_borda"], 1))
                p.setBrush(T["etq_bg"])
                p.drawRoundedRect(caixa, 10, 10)
                p.setPen(T["texto"])
                p.drawText(caixa, Qt.AlignCenter, texto)
                p.setOpacity(1.0)


if __name__ == "__main__":
    if "--restaurar" in sys.argv:       # usado pelo fechar.ps1 e pelo restaurar_barra.bat
        restaurar_barra(True)
        sys.exit(0)
    # so uma dock por vez (autostart + atalho nao abrem duas). Ao atualizar, a antiga ainda pode estar
    # saindo: espera ate ~10 s ela liberar antes de desistir.
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    for _ in range(40):
        _mutex = _k32.CreateMutexW(None, False, "Local\\DockApp_SingleInstance")
        if ctypes.get_last_error() != 183:      # 183 = ja existe outra dock aberta
            break
        _k32.CloseHandle(_mutex)
        time.sleep(0.25)
    else:
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)    # fechar o painel de preferencias nao fecha a dock
    provider = QFileIconProvider()
    atexit.register(restaurar_barra)
    app.aboutToQuit.connect(restaurar_barra)
    if PREFS["ocultar_barra"]:
        esconder_barra()
    dock = Dock()
    dock.show()
    dock.atualizar_vidro_visivel()
    dock.raise_()
    dock.atualizar_telas()               # uma dock em cada monitor extra

    def _telas_mudaram(*_):
        """Monitor ligado/desligado ou resolucao trocada: refaz as docks depois que o Windows assentar."""
        def refazer():
            for s in QGuiApplication.screens():
                try:
                    s.geometryChanged.disconnect(_telas_mudaram)
                except Exception:
                    pass
                s.geometryChanged.connect(_telas_mudaram)
            dock.atualizar_telas()
            dock.montar()
        QTimer.singleShot(500, refazer)

    app.screenAdded.connect(_telas_mudaram)
    app.screenRemoved.connect(_telas_mudaram)
    for _s in QGuiApplication.screens():
        _s.geometryChanged.connect(_telas_mudaram)

    # atalho global Ctrl+Alt+D (MOD_ALT | MOD_CONTROL | MOD_NOREPEAT + tecla D)
    filtro = FiltroTecla(dock.alternar_por_atalho)
    app.installNativeEventFilter(filtro)
    if user32.RegisterHotKey(None, ID_TECLA, 0x0001 | 0x0002 | 0x4000, 0x44):
        atexit.register(user32.UnregisterHotKey, None, ID_TECLA)
    sys.exit(app.exec())
