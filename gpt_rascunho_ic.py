# ============================================================
# SISTEMA DE ESPECTROSCOPIA AUTOMATIZADA (TMc300 + SR7265)
# Arquitetura Orientada a Objetos com Simulação Realista
# ============================================================

import time
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ============================================================
# CONFIGURAÇÃO GLOBAL
# ============================================================
SIMULATE = True   # Altere para False no computador do laboratório

# ============================================================
# CAMADA DE HARDWARE: MONOCROMADOR
# ============================================================
class BenthamMonochromator:
    def __init__(self):
        self.current_lambda = None
        self.lib = None

    def inicializar(self):
        if SIMULATE:
            print("[SIM] Monocromador Bentham inicializado.")
        else:
            import ctypes
            self.lib = ctypes.cdll.LoadLibrary("C:/Bentham/SDK/benhw64.dll")
            if self.lib.BI_initialise() != 0 or self.lib.BI_build_system(b"system.cfg", 0) != 0:
                raise RuntimeError("Erro grave de Hardware no TMc300.")

    def mover_para(self, lambda_nm):
        """Aplica correção de Backlash (folga mecânica) de 2nm ao descer o comprimento de onda."""
        OVERSHOOT = 2.0

        if SIMULATE:
            self.current_lambda = lambda_nm
            return lambda_nm

        # Lógica de Backlash (Física: Garantir que a engrenagem engate do mesmo lado)
        if self.current_lambda is not None and lambda_nm < self.current_lambda:
            temp_wl = max(0, lambda_nm - OVERSHOOT)
            self._enviar_comando(temp_wl) # Passa do ponto
            time.sleep(0.1)

        # Move para o ponto final
        wl_real = self._enviar_comando(lambda_nm)
        self.current_lambda = wl_real
        return wl_real

    def _enviar_comando(self, wl):
        import ctypes
        wl_req = ctypes.c_double(wl)
        wl_real = ctypes.c_double()
        self.lib.BI_select_wavelength(wl_req, ctypes.byref(wl_real))
        return wl_real.value

    def fechar(self):
        if not SIMULATE:
            self.lib.BI_park()
            self.lib.BI_close_system()

# ============================================================
# CAMADA DE HARDWARE: AMPLIFICADOR LOCK-IN
# ============================================================
class SR7265LockIn:
    def __init__(self):
        self.inst = None
        self.tau = 0.3 # Constante de tempo padrão

    def conectar(self, endereco="GPIB0::12::INSTR"):
        if SIMULATE:
            print("[SIM] Lock-in SR7265 conectado via GPIB simulado.")
        else:
            import pyvisa
            rm = pyvisa.ResourceManager()
            self.inst = rm.open_resource(endereco)
            self.inst.read_termination = '\r'
            print(f"Hardware Encontrado: {self.inst.query('*IDN?')}")

    def configurar_experimento(self, tau=0.3):
        self.tau = tau
        if SIMULATE:
            print(f"[SIM] Parâmetros ajustados: AC Coupling, Float Ground, FET, Tau={tau}s")
            return

        # Comandos extraídos do Apêndice F do manual SR7265
        self.inst.write("CP 0")      # Acoplamento AC (ignora luz ambiente constante)
        self.inst.write("FLOAT 1")   # Float Ground (evita loop de terra)
        self.inst.write("FET 1")     # Entrada FET (alta impedância)
        self.inst.write(f"TC. {tau}")# Constante de Filtro (em segundos)

    def ler_XY(self, wl):
        if SIMULATE:
            # Simula um espectro realista com dois picos (ex: emissão Raman)
            pico1 = 1.0e-3 * np.exp(-((wl - 500)**2) / 200)
            pico2 = 2.5e-3 * np.exp(-((wl - 650)**2) / 100)
            ruido = np.random.normal(0, 5e-5)
            x_val = pico1 + pico2 + ruido + 1e-4 # Sinal principal
            y_val = ruido * 0.5                  # Sinal fora de fase
            return x_val, y_val
        else:
            resp = self.inst.query("XY.")
            return map(float, resp.split(","))

    def verificar_status(self, wl):
        """Verifica sobrecarga (overload). Na simulação, força overload no pico maior."""
        if SIMULATE:
            is_overload = (640 < wl < 660) # Simula saturação perto do pico 2
            return {'overload': is_overload}

        status = int(self.inst.query("ST"))
        return {'overload': bool(status & 0x10)} # Bit 4 indica saturação

    def auto_sensitivity(self):
        if SIMULATE:
            print("[SIM] Executando Auto-Sensitivity... Escala ajustada.")
            return
        self.inst.write("AS")

    def fechar(self):
        if not SIMULATE:
            self.inst.close()

# ============================================================
# CONTROLADOR DO EXPERIMENTO (Orquestração)
# ============================================================
class Experimento:
    def __init__(self):
        self.mono = BenthamMonochromator()
        self.lockin = SR7265LockIn()
        self.dados = []

    def executar_varredura(self, start=400, end=800, step=5, tau=0.3):
        self.mono.inicializar()
        self.lockin.conectar()
        self.lockin.configurar_experimento(tau=tau)

        tempo_espera = tau * 5 # 5 vezes o tau é a regra de ouro física

        print("\n--- INICIANDO AQUISIÇÃO ---")
        try:
            for wl in range(start, end + 1, step):
                # 1. Ajuste Óptico
                wl_real = self.mono.mover_para(wl)

                # 2. Estabilização Termodinâmica do Filtro
                time.sleep(tempo_espera if not SIMULATE else 0.05)

                # 3. Tratamento de Saturação (Overload)
                status = self.lockin.verificar_status(wl_real)
                if status['overload']:
                    print(f" >> Saturação (Overload) detectada em {wl_real}nm! Ajustando escala...")
                    self.lockin.auto_sensitivity()
                    time.sleep(tempo_espera if not SIMULATE else 0.05)

                # 4. Aquisição de Dados
                x, y = self.lockin.ler_XY(wl_real)
                r = math.sqrt(x**2 + y**2)
                fase = math.degrees(math.atan2(y, x))

                print(f"λ: {wl_real:05.1f} nm | R: {r:.3e} V | Fase: {fase:05.1f}°")
                self.dados.append([wl_real, x, y, r, fase])

        finally:
            self.mono.fechar()
            self.lockin.fechar()
            print("--- AQUISIÇÃO FINALIZADA ---")

    def salvar_e_plotar(self):
        df = pd.DataFrame(self.dados, columns=["Lambda(nm)", "X(V)", "Y(V)", "R(V)", "Fase(deg)"])
        df.to_csv("espectro.csv", index=False)

        plt.figure(figsize=(10, 5))
        plt.plot(df["Lambda(nm)"], df["R(V)"], 'b-o', markersize=4, label='Magnitude (R)')
        plt.fill_between(df["Lambda(nm)"], df["R(V)"], color='blue', alpha=0.1)
        plt.title("Espectro Óptico Simulado com Tratamento de Overload")
        plt.xlabel("Comprimento de Onda (nm)")
        plt.ylabel("Intensidade do Sinal (Volts)")
        plt.grid(True, linestyle='--', alpha=0.7)
        plt.legend()
        plt.show()

# ============================================================
# EXECUÇÃO
# ============================================================
if __name__ == "__main__":
    exp = Experimento()
    exp.executar_varredura(start=400, end=800, step=5, tau=0.3)
    exp.salvar_e_plotar()