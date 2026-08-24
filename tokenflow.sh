#!/usr/bin/env bash
# ==============================================================================
#                 TokenFlow Host CLI Management Utility
# ==============================================================================
set -e

INSTALL_DIR="${INSTALL_DIR:-/opt/tokenflow}"
CONFIG_FILE="$INSTALL_DIR/config.yaml"
SERVICE_NAME="tokenflow"

COLOR_GREEN="\033[32m"
COLOR_RED="\033[31m"
COLOR_YELLOW="\033[33m"
COLOR_CYAN="\033[36m"
COLOR_RESET="\033[0m"

usage() {
    echo -e "${COLOR_CYAN}TokenFlow Service CLI Manager${COLOR_RESET}"
    echo ""
    echo "Usage: tokenflow [command]"
    echo ""
    echo "Available Commands:"
    echo "  start       - Start the TokenFlow systemd service"
    echo "  stop        - Stop the TokenFlow service"
    echo "  restart     - Restart the TokenFlow service"
    echo "  status      - Display current running status and ports"
    echo "  logs        - View real-time aggregated service logs"
    echo "  config      - Open and edit TokenFlow configuration"
    echo "  help        - Display this help message"
    echo ""
}

CMD="$1"

case "$CMD" in
    start)
        echo -e "${COLOR_GREEN}Starting TokenFlow service...${COLOR_RESET}"
        systemctl start "$SERVICE_NAME"
        systemctl status "$SERVICE_NAME" --no-pager
        ;;
    stop)
        echo -e "${COLOR_YELLOW}Stopping TokenFlow service...${COLOR_RESET}"
        systemctl stop "$SERVICE_NAME"
        ;;
    restart)
        echo -e "${COLOR_GREEN}Restarting TokenFlow service...${COLOR_RESET}"
        systemctl restart "$SERVICE_NAME"
        systemctl status "$SERVICE_NAME" --no-pager
        ;;
    status)
        echo -e "${COLOR_CYAN}Checking TokenFlow status...${COLOR_RESET}"
        systemctl status "$SERVICE_NAME" --no-pager || true
        echo ""
        echo "Listening Ports Check:"
        ss -tulpn 2>/dev/null | grep -E "18317|8081|4646|9090" || netstat -tulpn 2>/dev/null | grep -E "18317|8081|4646|9090" || true
        ;;
    logs)
        echo -e "${COLOR_CYAN}Tailing TokenFlow logs (Ctrl+C to exit)...${COLOR_RESET}"
        journalctl -u "$SERVICE_NAME" -f -n 100
        ;;
    config|edit)
        EDITOR="${EDITOR:-nano}"
        if [ ! -f "$CONFIG_FILE" ]; then
            if [ -f "$INSTALL_DIR/config.example.yaml" ]; then
                cp "$INSTALL_DIR/config.example.yaml" "$CONFIG_FILE"
            fi
        fi
        $EDITOR "$CONFIG_FILE"
        echo -e "${COLOR_YELLOW}Config edited. To apply changes, run: tokenflow restart${COLOR_RESET}"
        ;;
    *)
        usage
        ;;
esac
