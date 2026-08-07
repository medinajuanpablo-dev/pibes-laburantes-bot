# Empezá acá

Esto es el bot del grupo: alguien pega un link de YouTube, Instagram o Facebook y el bot responde
con el video o la foto, para verlo sin salir del chat.

El bot **solo funciona mientras alguien lo tiene prendido en su compu**. Por eso nos vamos turnando:
el que está a mano lo prende, y cuando lo apaga el grupo se queda sin bot hasta que lo prenda otro.

**Solo una persona a la vez.** Telegram no deja que dos lo tengan prendido al mismo tiempo. Si
alguien más lo tiene, el programa te avisa y te pregunta si se lo querés sacar.

---

## La primera vez

### 1. Bajar la carpeta del bot

Bajala con `git`, **no la bajes como ZIP**. Es un paso más la primera vez y te ahorra dos problemas:
así se actualiza sola, y así tu compu no la trata como "programa sospechoso de internet".

**En Mac:** abrí la app **Terminal** (Cmd+Espacio, escribí `Terminal`, Enter), pegá esto y apretá
Enter:

```
cd ~/Documents && git clone https://github.com/medinajuanpablo-dev/pibes-laburantes-bot.git
```

Si te aparece una ventana que dice que faltan las "herramientas de línea de comandos", aceptá,
esperá a que termine y volvé a pegar el comando.

**En Windows:** instalá Git desde <https://git-scm.com/download/win>, después abrí **Git Bash** y
pegá el mismo comando.

Te queda una carpeta llamada `pibes-laburantes-bot`.

### 2. Pedir el token

El token es la llave del bot: una línea larga de letras y números. **Pedísela al dueño por privado.**
No la pegues en el grupo ni se la pases a nadie.

### 3. Prenderlo

Entrá a la carpeta y hacé doble clic en:

- **Mac:** `run-bot.command`
- **Windows:** `run-bot.cmd`

Se abre una ventana negra con texto. Eso está bien, es así. La primera vez tarda un minuto porque
prepara todo, y te va a pedir el token: pegalo y apretá Enter. Queda guardado, no te lo pide de
nuevo.

Si te falta algo (Python o ffmpeg), la ventana te dice en una línea qué instalar y de dónde.

---

## Cada vez

Doble clic en el mismo archivo. Se actualiza sola, se fija si alguien más lo tiene prendido, y
arranca.

**Dejá la ventana abierta.** Mientras esté abierta, el bot anda.

Para apagarlo: apretá **Control-C** en esa ventana, o cerrala.

**Lo que se postea mientras nadie lo tiene prendido no llega.** No se acumula para después: si
pegaste un link con el bot apagado, pegalo de nuevo cuando alguien lo prenda.

Si en la ventana aparece **"Otra persona prendió el bot"**, es exactamente eso: alguien lo prendió y
te lo sacó. Es normal, no rompiste nada. Al ratito el bot se apaga solo y podés cerrar la ventana.

---

## Si tu compu dice que el archivo es peligroso

Esto pasa **solo si bajaste la carpeta como ZIP en vez de usar `git clone`**. Si la clonaste con
git, no te va a pasar nunca.

**En Mac** el doble clic no hace nada, o aparece una ventana diciendo que el archivo *"proviene de
un desarrollador no identificado"*, o que *"Apple no pudo verificar"* que no tenga software
malicioso. (Comprobado en macOS 15.1: el archivo marcado como bajado de internet directamente no
arranca; el mismo archivo traído con `git clone` abre sin decir nada.)

**En Windows** aparece una pantalla azul de **SmartScreen**: *"Windows protegió su PC"*.

En los dos casos la solución es la misma y es la de arriba: **borrá lo que bajaste y traelo con
`git clone`**. Los archivos que baja git no quedan marcados como "descargados de internet", así que
no aparece ninguna de esas ventanas. Si igual querés abrir el que ya bajaste, en Mac es clic
derecho sobre el archivo → **Abrir**, y en Windows es **Más información** → **Ejecutar de todas
formas**.

---

## Dudas frecuentes

**¿Me va a andar más lento la compu?** No. El bot está quieto casi todo el tiempo; solo trabaja unos
segundos cuando alguien pega un link.

**¿Puedo apagarlo cuando quiera?** Sí, cuando quieras, sin avisar. El grupo se queda sin bot hasta
que otro lo prenda.

**¿Y si lo prendemos dos al mismo tiempo?** No se puede: el segundo te avisa que ya está prendido y
te pregunta si se lo querés sacar. Al que se lo sacaste, la ventana le avisa y el bot se le apaga
solo.

**El bot no responde a un link.** Puede ser que sea de un sitio que no maneja (solo YouTube,
Instagram y Facebook), o que el link no arranque con `http`. También puede ser que nadie lo tenga
prendido.

**Se me cerró la ventana sin que yo hiciera nada.** Volvé a abrir el archivo. Si vuelve a pasar,
avisale al dueño.
