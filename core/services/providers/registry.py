from __future__ import annotations

from core.services.providers.base import SourceProvider
from core.services.providers.wolfmax_provider import WolfmaxSourceProvider


def build_provider_registry(
    db_manager, *, hispashare_driver=None, include_wolfmax: bool = True
) -> dict[str, SourceProvider]:
    """Construye el registro de proveedores activos.

    Añadir un proveedor nuevo (torrent, aMule directo, ...) = una clase que
    implemente SourceProvider + una entrada aquí. Nada del arbitraje o de
    scheduler.py necesita cambiar.

    `hispashare_driver` es opcional porque Hispashare requiere una sesión
    Selenium ya autenticada (creada una vez por ciclo en scheduler.py); si no
    se pasa, ese proveedor simplemente no se registra.

    `include_wolfmax` preserva el comportamiento previo del scheduler, donde
    Settings.WOLFMAX_UPGRADE_ENABLED apagaba Wolfmax tanto para upgrades como
    para descargas de episodios faltantes.
    """
    providers: dict[str, SourceProvider] = {}

    if include_wolfmax:
        providers["wolfmax"] = WolfmaxSourceProvider(db_manager)

    if hispashare_driver is not None:
        from core.services.providers.hispashare_provider import HispashareSourceProvider

        providers["hispashare"] = HispashareSourceProvider(hispashare_driver, db_manager)

    return providers
