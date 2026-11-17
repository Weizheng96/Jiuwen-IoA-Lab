"""Frozen SDK tools coexist with the user's native tools at the native boundary."""
import asyncio

from test_jiuwen_native_turn import module, agents, section, tool, execution, ROUTE, ECHO, model, call


def test_native_turn_routes_multiple_sdk_tools_and_cleans_only_its_own_tools():
    async def scenario():
        native = module()
        owner, agent = agents()
        seen = []
        declarations = [dict(ECHO, name=name) for name in
                        ('provider.task.request_step', 'provider.task.selftest', 'provider.task.deliver')]
        async def callback(call_id, name, arguments):
            seen.append((call_id, name, arguments))
            return {'value': {'tool': name}, 'error': None}
        turn = native.PreparedTurn(*ROUTE, execution(tools=declarations), section, tool, callback)
        turn.reserve(owner, agent)
        turn.on_invoke(owner, ROUTE)
        turn.accept(True)
        window = model(agent, 'native_write')
        turn.on_model(window, ROUTE)
        assert {item.name for item in window.inputs.tools} == {'native_write', *(item['name'] for item in declarations)}
        for index, declaration in enumerate(declarations):
            ctx = call(agent, declaration['name'], str(index))
            turn.on_tool(ctx, ROUTE)
            assert await agent.ability_manager.call(ctx.inputs) == {'tool': declaration['name']}
            turn.after_tool(ctx)
        assert [name for _, name, _ in seen] == [item['name'] for item in declarations]
        turn.close()
        assert not agent.ability_manager.tools and set(agent.prompt_builder.sections) == {'user'}
    asyncio.run(scenario())
