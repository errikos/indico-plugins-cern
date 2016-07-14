(function() {
    'use strict';
    var $t = $T.domain('ravem');
    var ravemButton = (function makeRavemButton() {
        var states = {
            connected: {
                icon: 'icon-no-camera',
                tooltip: $t.gettext('Disconnect {0} from the Vidyo room {1}'),
                action: 'disconnect',
                handler: function disconnectHandler(data, btn) {
                    var name = btn.data('roomName');
                    var vcRoomName = btn.data('vcRoomName');
                    var requestStates = {
                        old: 'connected',
                        new: 'disconnected',
                        error: 'errorDisconnect',
                        wait: 'waitingDisconnect'
                    };
                    var messages = {
                        alreadyConnected: $t.gettext(
                            'Would you like to force the room {0} to disconnect?').format(name),
                        error:  $t.gettext(
                            'The room {0} might already be disconnected or connected to another Vidyo room'
                        ).format(name),
                    };

                    _handler(data, btn, requestStates, ['already-disconnected'], messages,
                             function checkDisconnected(status) {
                                 return !status.connected || status.vc_room_name !== vcRoomName;
                    });
                }
            },
            disconnected: {
                icon: 'icon-camera',
                tooltip: $t.gettext('Connect {0} to the Vidyo room {1}'),
                action: 'connect',
                handler: function connectHandler(data, btn) {
                    var name = btn.data('roomName');
                    var vcRoomName = btn.data('vcRoomName');
                    var requestStates = {
                        old: 'disconnected',
                        new: 'connected',
                        error: 'errorConnect',
                        wait: 'waitingConnect'
                    };
                    var messages = {
                        alreadyConnected: $t.gettext(
                            'Would you like to force the room {0} to connect to your Vidyo room ({1}) ?'
                        ).format(name, vcRoomName),
                        error:  $t.gettext('The room {0} might already be connected to another Vidyo room').format(name)
                    };

                    _handler(data, btn, requestStates, ['already-connected'], messages,
                             function checkConnect(status){
                                 return status.connected && status.vc_room_name === vcRoomName;
                    });
                }
            },
            errorConnect: {
                tooltip: $t.gettext("Unable to connect<br>{2}Please wait a moment and refresh the page to try again."),
                tooltipType: 'error',
                icon: 'icon-warning'
            },
            errorDisconnect: {
                tooltip: $t.gettext(
                    "Unable to disconnect<br>{2}Please wait a moment and refresh the page to try again."),
                tooltipType: 'error',
                icon: 'icon-warning'
            },
            errorStatus: {
                tooltip: $t.gettext(
                    "Unable to contact the room.<br>{2}Please wait a moment and refresh the page to try again."),
                tooltipType: 'error',
                icon: 'icon-warning'
            },
            waitingConnect: {
                icon: 'icon-spinner',
                tooltip: $t.gettext("Connecting {0} to the Vidyo room {1}")
            },
            waitingDisconnect: {
                icon: 'icon-spinner',
                tooltip: $t.gettext("Disconnecting {0} from the Vidyo room {1}")
            },
            waitingStatus: {
                icon: 'icon-spinner',
                tooltip: $t.gettext("Waiting for information about {0}")
            }
        };

        /**
         * Base handler to handle the result of a (connect/disconnect) request.
         */
        function _handler(data, btn, requestStates, validReasons, messages, checkFn) {
            var name = btn.data('roomName');

            // If the request appears to be successful, we now  must poll RAVEM
            // through Indico to assert it was
            if (data.success) {
                var attempts = RavemPlugin.polling.limit;
                var timer = window.setTimeout(function assertActionSuccessful() {
                    // Out of polling attempts without the expected status,
                    // we assume the request failed.
                    if (!attempts) {
                        setButtonState(btn, requestStates.error);
                        return;
                    }
                    getRoomStatus(btn)
                        // Failure when getting the status.
                        .fail(function statusUpdateErrorHandler(error) {
                            attempts--;
                            // Out of polling attempts, we assume the request failed.
                            if (!attempts) {
                                setButtonState(btn, requestStates.error, error.message || messages.error);
                                return;
                            }

                            // Try to poll again after the given interval
                            timer = window.setTimeout(assertActionSuccessful, RavemPlugin.polling.interval);
                        })
                        .done(function statusUpdateHandler(status) {
                            // Failed to get the status or the status is not what was expected
                            if (!status.success || !checkFn(status)) {
                                attempts--;
                                // Out of polling attempts, we assume the request failed.
                                if (!attempts) {
                                    setButtonState(btn, requestStates.error, status.message || messages.error);
                                    return;
                                }

                                // Try to poll again after the given interval
                                timer = window.setTimeout(assertActionSuccessful, RavemPlugin.polling.interval);
                            // Got the expected status, move the button to the new state
                            } else {
                                setButtonState(btn, requestStates.new);
                            }
                    });
                }, RavemPlugin.polling.interval);

            // request failed with some reason and this reason is among the
            // valid reasons so we mvoe the button to the new state.
            } else if (~_.indexOf(validReasons, data.reason)) {
                setButtonState(btn, requestStates.new);

            // The room is connected to another VC room, we ask the user for
            // confirmation if he wants to reiterate the previous request and
            // force it (force connect/force disconnect)
            } else if (data.reason === 'connected-other') {
                new ConfirmPopup(
                    $t.gettext('{0} already connected').format(name), [
                        data.message.replace('\n', '<br>'),
                        messages.alreadyConnected
                    ].join('<br>'),
                    function forceRequestPopup(force) {
                        setButtonState(btn, requestStates.old);
                        if (force) {
                            sendRequest(btn, requestStates.wait, true)
                            .always(function(newData) {
                                _handler(newData, btn, requestStates, validReasons, messages, checkFn);
                            });
                        }
                }).open();

            } else {
                setButtonState(btn, requestStates.error, data.message);
            }
        }

        /**
         *  Sets a new state for the button and update its icon, label and tool tip.
         */
        function setButtonState(btn, newState, tooltipMessage) {
            btn.data('state', newState);

            var name = btn.data('roomName');
            var vcRoomName = btn.data('vcRoomName');
            var html = ['<span class="', states[newState].icon,
                        '"><strong style="margin-left: 0.4em;">{0}</strong></span>'.format(name)].join('');

            btn.html(html);
            btn.toggleClass('disabled', !states[newState].action); // Whether the button should be disabled

            tooltipMessage = tooltipMessage ? tooltipMessage + '<br>' : '';
            var qtip = {
                content: states[newState].tooltip.format(name, vcRoomName, tooltipMessage),
                position: {my: 'top center', at: 'bottom center'},
                show: 'mouseover', hide: {
                    event: 'mouseout',
                    leave: false,
                    fixed: true,
                    delay: 500
                }
            };
            if ('tooltipType' in states[newState]) {
                qtip.style = {classes: 'qtip-' + states[newState].tooltipType};
            }
            btn.qtip('destroy', true);
            btn.qtip(qtip);
        }

        function clickHandler(e) {
            e.preventDefault();
            var btn = $(this);

            if (btn.hasClass('disabled')) {
                return;
            }

            var state = btn.data('state');
            switch (state) {
                case 'connected':
                    sendRequest(btn, 'waitingDisconnect')
                        .always(function onConnect(data) {
                            states.connected.handler(data, btn);
                        });
                    break;
                case 'disconnected':
                    var msg = $T.gettext('Ready to join the conference room?');
                    confirmPrompt(msg).then(function() {
                        sendRequest(btn, 'waitingConnect')
                            .always(function onDisconnect(data) {
                                states.disconnected.handler(data, btn);
                            });
                    });
                    break;
                default:
                    new ErrorPopup(
                        $t.gettext("Something went wrong."),
                        [$t.gettext("Unknown error")],
                        $t.gettext("Please refresh the page and try again.")
                    ).open();
            }
        }

        /**
         * Base function which sends an ajax request ad captures possible errors.
         */
        function _sendRequest(btn, type, url, waitingState) {
            if (waitingState) {
                setButtonState(btn, waitingState);
            }

            var deferred = $.Deferred();
            $.ajax({
                type: type,
                url: url,
                error: function errorHandler(data) {
                    var errMsg;
                    try {
                        var response = JSON.parse(data.responseText);
                        errMsg = response.error.message;
                    } catch(e) {
                        errMsg = $t.gettext('unknown error');
                    }
                    deferred.reject({success: false, message: errMsg});
                },
                success: function successHandler(data) {
                    deferred.resolve(data);
                }
            });
            return deferred.promise();
        }

        /**
         * Sends a POST request to Indico according to the action attached to
         * the button's current the state.
         */
        function sendRequest(btn, waitingState, force) {
            force = force || false;
            var state = btn.data('state');
            var url = btn.data(states[state].action + 'Url');
            if (force) {
                url = build_url(url, {force: '1'});
            }

            return _sendRequest(btn, 'POST', url, waitingState);
        }

        function getRoomStatus(btn, waitingState) {
            return _sendRequest(btn, 'GET', btn.data('statusUrl'), waitingState);
        }

        function initializeRavemButton(btn) {
            btn.on('click', clickHandler);
            var vcRoomName = btn.data('vcRoomName');
            getRoomStatus(btn, 'waitingStatus')
                .fail(function errorHandler(data) {
                    setButtonState(btn, 'errorStatus', data.message);
                })
                .done(function successHandler(data) {
                    if (!data.success) {
                        setButtonState(btn, 'errorStatus', data.message);
                    } else {
                        var connected = data.connected && data.vc_room_name === vcRoomName;
                        setButtonState(btn, connected ? 'connected' : 'disconnected');
                    }
                });
            return btn;
        }

        return initializeRavemButton;
    })();

    $(document).ready(function() {
        $('.js-ravem-button').each(function() {
            ravemButton($(this));
        });
    });
})();
