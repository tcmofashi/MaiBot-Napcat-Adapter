from maim_message import GroupInfo
from typing import Any, Dict, Tuple, Callable, Optional

from src import CommandType


# 全局命令处理器注册表（在类外部定义以避免循环引用）
_command_handlers: Dict[str, Dict[str, Any]] = {}


def register_command(command_type: CommandType, require_group: bool = True):
    """装饰器：注册命令处理器

    Args:
        command_type: 命令类型
        require_group: 是否需要群聊信息，默认为True

    Returns:
        装饰器函数
    """

    def decorator(func: Callable) -> Callable:
        _command_handlers[command_type.name] = {
            "handler": func,
            "require_group": require_group,
        }
        return func

    return decorator


class SendCommandHandleClass:
    @classmethod
    def handle_command(cls, raw_command_data: Dict[str, Any], group_info: Optional[GroupInfo]):
        """统一命令处理入口

        Args:
            raw_command_data: 原始命令数据
            group_info: 群聊信息（可选）

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params) 用于发送给NapCat

        Raises:
            RuntimeError: 命令类型未知或处理失败
        """
        command_name: str = raw_command_data.get("name")

        if command_name not in _command_handlers:
            raise RuntimeError(f"未知的命令类型: {command_name}")

        try:
            handler_info = _command_handlers[command_name]
            handler = handler_info["handler"]
            require_group = handler_info["require_group"]

            # 检查群聊信息要求
            if require_group and not group_info:
                raise ValueError(f"命令 {command_name} 需要在群聊上下文中使用")

            # 调用处理器
            args = raw_command_data.get("args", {})
            return handler(args, group_info)

        except Exception as e:
            raise RuntimeError(f"处理命令 {command_name} 时出错: {str(e)}") from e

    # ============ 命令处理器（使用装饰器注册）============

    @staticmethod
    @register_command(CommandType.GROUP_BAN, require_group=True)
    def handle_ban_command(args: Dict[str, Any], group_info: Optional[GroupInfo]) -> Tuple[str, Dict[str, Any]]:
        """处理封禁命令

        Args:
            args: 参数字典 {"qq_id": int, "duration": int}
            group_info: 群聊信息（对应目标群聊）

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params)
        """
        duration: int = int(args["duration"])
        user_id: int = int(args["qq_id"])
        group_id: int = int(group_info.group_id)
        if duration < 0:
            raise ValueError("封禁时间必须大于等于0")
        if not user_id or not group_id:
            raise ValueError("封禁命令缺少必要参数")
        if duration > 2592000:
            raise ValueError("封禁时间不能超过30天")
        return (
            CommandType.GROUP_BAN.value,
            {
                "group_id": group_id,
                "user_id": user_id,
                "duration": duration,
            },
        )

    @staticmethod
    @register_command(CommandType.GROUP_WHOLE_BAN, require_group=True)
    def handle_whole_ban_command(args: Dict[str, Any], group_info: Optional[GroupInfo]) -> Tuple[str, Dict[str, Any]]:
        """处理全体禁言命令

        Args:
            args: 参数字典 {"enable": bool}
            group_info: 群聊信息（对应目标群聊）

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params)
        """
        enable = args["enable"]
        assert isinstance(enable, bool), "enable参数必须是布尔值"
        group_id: int = int(group_info.group_id)
        if group_id <= 0:
            raise ValueError("群组ID无效")
        return (
            CommandType.GROUP_WHOLE_BAN.value,
            {
                "group_id": group_id,
                "enable": enable,
            },
        )

    @staticmethod
    @register_command(CommandType.GROUP_KICK, require_group=True)
    def handle_kick_command(args: Dict[str, Any], group_info: Optional[GroupInfo]) -> Tuple[str, Dict[str, Any]]:
        """处理群成员踢出命令

        Args:
            args: 参数字典 {"qq_id": int}
            group_info: 群聊信息（对应目标群聊）

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params)
        """
        user_id: int = int(args["qq_id"])
        group_id: int = int(group_info.group_id)
        if group_id <= 0:
            raise ValueError("群组ID无效")
        if user_id <= 0:
            raise ValueError("用户ID无效")
        return (
            CommandType.GROUP_KICK.value,
            {
                "group_id": group_id,
                "user_id": user_id,
                "reject_add_request": False,  # 不拒绝加群请求
            },
        )

    @staticmethod
    @register_command(CommandType.SEND_POKE, require_group=False)
    def handle_poke_command(args: Dict[str, Any], group_info: Optional[GroupInfo]) -> Tuple[str, Dict[str, Any]]:
        """处理戳一戳命令

        Args:
            args: 参数字典 {"qq_id": int}
            group_info: 群聊信息（可选，私聊时为None）

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params)
        """
        user_id: int = int(args["qq_id"])
        if group_info is None:
            group_id = None
        else:
            group_id: int = int(group_info.group_id)
            if group_id <= 0:
                raise ValueError("群组ID无效")
        if user_id <= 0:
            raise ValueError("用户ID无效")
        return (
            CommandType.SEND_POKE.value,
            {
                "group_id": group_id,
                "user_id": user_id,
            },
        )

    @staticmethod
    @register_command(CommandType.DELETE_MSG, require_group=False)
    def delete_msg_command(args: Dict[str, Any], group_info: Optional[GroupInfo]) -> Tuple[str, Dict[str, Any]]:
        """处理撤回消息命令

        Args:
            args: 参数字典 {"message_id": int}
            group_info: 群聊信息（不使用）

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params)
        """
        try:
            message_id = int(args["message_id"])
            if message_id <= 0:
                raise ValueError("消息ID无效")
        except KeyError:
            raise ValueError("缺少必需参数: message_id") from None
        except (ValueError, TypeError) as e:
            raise ValueError(f"消息ID无效: {args['message_id']} - {str(e)}") from None

        return (
            CommandType.DELETE_MSG.value,
            {
                "message_id": message_id,
            },
        )

    @staticmethod
    @register_command(CommandType.AI_VOICE_SEND, require_group=True)
    def handle_ai_voice_send_command(args: Dict[str, Any], group_info: Optional[GroupInfo]) -> Tuple[str, Dict[str, Any]]:
        """处理AI语音发送命令

        Args:
            args: 参数字典 {"character": str, "text": str}
            group_info: 群聊信息

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params)
        """
        if not group_info or not group_info.group_id:
            raise ValueError("AI语音发送命令必须在群聊上下文中使用")
        if not args:
            raise ValueError("AI语音发送命令缺少参数")

        group_id: int = int(group_info.group_id)
        character_id = args.get("character")
        text_content = args.get("text")

        if not character_id or not text_content:
            raise ValueError(f"AI语音发送命令参数不完整: character='{character_id}', text='{text_content}'")

        return (
            CommandType.AI_VOICE_SEND.value,
            {
                "group_id": group_id,
                "text": text_content,
                "character": character_id,
            },
        )

    @staticmethod
    @register_command(CommandType.MESSAGE_LIKE, require_group=False)
    def handle_message_like_command(args: Dict[str, Any], group_info: Optional[GroupInfo]) -> Tuple[str, Dict[str, Any]]:
        """处理给消息贴表情命令

        Args:
            args: 参数字典 {"message_id": int, "emoji_id": int}
            group_info: 群聊信息（不使用）

        Returns:
            Tuple[str, Dict[str, Any]]: (action, params)
        """
        if not args:
            raise ValueError("消息贴表情命令缺少参数")

        message_id = args.get("message_id")
        emoji_id = args.get("emoji_id")
        if not message_id:
            raise ValueError("消息贴表情命令缺少必要参数: message_id")
        if not emoji_id:
            raise ValueError("消息贴表情命令缺少必要参数: emoji_id")

        message_id = int(message_id)
        emoji_id = int(emoji_id)
        if message_id <= 0:
            raise ValueError("消息ID无效")
        if emoji_id <= 0:
            raise ValueError("表情ID无效")

        return (
            CommandType.MESSAGE_LIKE.value,
            {
                "message_id": message_id,
                "emoji_id": emoji_id,
                "set": True,
            },
        )
