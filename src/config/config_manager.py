"""配置管理器 - 支持热重载"""
import asyncio
import os
from typing import Callable, Dict, List, Any, Optional
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

from ..logger import logger
from .config import Config, load_config


class ConfigManager:
    """配置管理器 - 混合模式（属性代理 + 选择性回调）
    
    支持热重载配置文件，使用watchdog实时监控文件变化。
    需要特殊处理的配置项可以注册回调函数。
    """

    def __init__(self) -> None:
        self._config: Optional[Config] = None
        self._config_path: str = "config.toml"
        self._lock: asyncio.Lock = asyncio.Lock()
        self._callbacks: Dict[str, List[Callable]] = {}
        
        # Watchdog相关
        self._observer: Optional[Observer] = None
        self._event_handler: Optional[FileSystemEventHandler] = None
        self._reload_debounce_task: Optional[asyncio.Task] = None
        self._debounce_delay: float = 0.5  # 防抖延迟（秒）
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # 事件循环引用
        self._is_reloading: bool = False  # 标记是否正在重载
        self._last_reload_trigger: float = 0.0  # 最后一次触发重载的时间

    def load(self, config_path: str = "config.toml") -> None:
        """加载配置文件
        
        Args:
            config_path: 配置文件路径
        """
        self._config_path = os.path.abspath(config_path)
        self._config = load_config(config_path)
        
        logger.info(f"配置已加载: {config_path}")

    async def reload(self, config_path: Optional[str] = None) -> bool:
        """重载配置文件（热重载）
        
        Args:
            config_path: 配置文件路径，如果为None则使用初始路径
            
        Returns:
            bool: 是否重载成功
        """
        if config_path is None:
            config_path = self._config_path

        async with self._lock:
            old_config = self._config
            try:
                new_config = load_config(config_path)
                
                if old_config is not None:
                    await self._notify_changes(old_config, new_config)
                
                self._config = new_config
                logger.info(f"配置重载成功: {config_path}")
                return True
                
            except Exception as e:
                logger.error(f"配置重载失败: {e}", exc_info=True)
                return False

    def on_config_change(
        self, 
        config_path: str, 
        callback: Callable[[Any, Any], Any]
    ) -> None:
        """为特定配置路径注册回调函数
        
        Args:
            config_path: 配置路径，如 'napcat_server', 'chat.ban_user_id', 'debug.level'
            callback: 回调函数，签名为 async def callback(old_value, new_value)
        """
        if config_path not in self._callbacks:
            self._callbacks[config_path] = []
        self._callbacks[config_path].append(callback)
        logger.debug(f"已注册配置变更回调: {config_path}")

    async def _notify_changes(self, old_config: Config, new_config: Config) -> None:
        """通知配置变更
        
        Args:
            old_config: 旧配置对象
            new_config: 新配置对象
        """
        for config_path, callbacks in self._callbacks.items():
            try:
                old_value = self._get_value(old_config, config_path)
                new_value = self._get_value(new_config, config_path)
                
                if old_value != new_value:
                    logger.info(f"检测到配置变更: {config_path}")
                    for callback in callbacks:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(old_value, new_value)
                            else:
                                callback(old_value, new_value)
                        except Exception as e:
                            logger.error(
                                f"配置变更回调执行失败 [{config_path}]: {e}",
                                exc_info=True
                            )
            except Exception as e:
                logger.error(f"获取配置值失败 [{config_path}]: {e}")

    def _get_value(self, config: Config, path: str) -> Any:
        """获取嵌套配置值
        
        Args:
            config: 配置对象
            path: 配置路径，支持点分隔的嵌套路径
            
        Returns:
            Any: 配置值
            
        Raises:
            AttributeError: 配置路径不存在
        """
        parts = path.split('.')
        value = config
        for part in parts:
            value = getattr(value, part)
        return value

    def __getattr__(self, name: str) -> Any:
        """动态代理配置属性访问
        
        支持直接访问配置对象的属性，如：
        - config_manager.napcat_server
        - config_manager.chat
        - config_manager.debug
        
        Args:
            name: 属性名
            
        Returns:
            Any: 配置对象的对应属性值
            
        Raises:
            RuntimeError: 配置尚未加载
            AttributeError: 属性不存在
        """
        # 私有属性不代理
        if name.startswith('_'):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        
        # 检查配置是否已加载
        if self._config is None:
            raise RuntimeError("配置尚未加载，请先调用 load() 方法")
        
        # 尝试从 _config 获取属性
        try:
            return getattr(self._config, name)
        except AttributeError as e:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            ) from e

    async def start_watch(self) -> None:
        """启动配置文件监控（需要在事件循环中调用）"""
        if self._observer is not None:
            logger.warning("配置文件监控已在运行")
            return
        
        # 保存当前事件循环引用
        self._loop = asyncio.get_running_loop()
        
        # 创建文件监控事件处理器
        config_file_path = self._config_path
        
        class ConfigFileHandler(FileSystemEventHandler):
            def __init__(handler_self, manager: "ConfigManager"):
                handler_self.manager = manager
                handler_self.config_path = config_file_path
            
            def on_modified(handler_self, event):
                # 检查是否是目标配置文件修改事件
                if isinstance(event, FileModifiedEvent) and os.path.abspath(event.src_path) == handler_self.config_path:
                    logger.debug(f"检测到配置文件变更: {event.src_path}")
                    # 使用防抖机制避免重复重载
                    # watchdog运行在独立线程，需要使用run_coroutine_threadsafe
                    if handler_self.manager._loop:
                        asyncio.run_coroutine_threadsafe(
                            handler_self.manager._debounced_reload(),
                            handler_self.manager._loop
                        )
        
        self._event_handler = ConfigFileHandler(self)
        
        # 创建Observer并监控配置文件所在目录
        self._observer = Observer()
        watch_dir = os.path.dirname(self._config_path) or "."
        
        self._observer.schedule(self._event_handler, watch_dir, recursive=False)
        self._observer.start()
        
        logger.info(f"已启动配置文件实时监控: {self._config_path}")

    async def stop_watch(self) -> None:
        """停止配置文件监控"""
        if self._observer is None:
            return
        
        logger.debug("正在停止配置文件监控")
        
        # 取消防抖任务
        if self._reload_debounce_task:
            self._reload_debounce_task.cancel()
            try:
                await self._reload_debounce_task
            except asyncio.CancelledError:
                pass
        
        # 停止observer
        self._observer.stop()
        self._observer.join(timeout=2)
        self._observer = None
        self._event_handler = None
        
        logger.info("配置文件监控已停止")

    async def _debounced_reload(self) -> None:
        """防抖重载：避免短时间内多次文件修改事件导致重复重载"""
        import time
        
        # 记录当前触发时间
        trigger_time = time.time()
        self._last_reload_trigger = trigger_time
        
        # 等待防抖延迟
        await asyncio.sleep(self._debounce_delay)
        
        # 检查是否有更新的触发
        if self._last_reload_trigger > trigger_time:
            # 有更新的触发，放弃本次重载
            logger.debug("放弃过时的重载请求")
            return
        
        # 检查是否已有重载在进行
        if self._is_reloading:
            logger.debug("重载已在进行中，跳过")
            return
        
        # 执行重载
        self._is_reloading = True
        try:
            modified_time = datetime.fromtimestamp(
                os.path.getmtime(self._config_path)
            ).strftime("%Y-%m-%d %H:%M:%S")
            
            logger.info(
                f"配置文件已更新 (修改时间: {modified_time})，正在重载..."
            )
            
            success = await self.reload()
            
            if not success:
                logger.error(
                    "配置文件重载失败！请检查配置文件格式是否正确。\n"
                    "当前仍使用旧配置运行，修复配置文件后将自动重试。"
                )
        finally:
            self._is_reloading = False

    def __repr__(self) -> str:
        watching = self._observer is not None and self._observer.is_alive()
        return f"<ConfigManager config_path={self._config_path} watching={watching}>"
