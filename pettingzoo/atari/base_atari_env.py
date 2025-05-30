from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, NewType, cast

import gymnasium
import multi_agent_ale_py  # type: ignore[import-untyped]
import numpy as np
import numpy.typing as npt
import pygame
from gymnasium import spaces
from gymnasium.utils import EzPickle, seeding

from pettingzoo import AECEnv
from pettingzoo.utils import wrappers
from pettingzoo.utils.conversions import parallel_to_aec_wrapper, parallel_wrapper_fn
from pettingzoo.utils.env import ParallelEnv

__all__ = [
    "parallel_wrapper_fn",
    "parallel_to_aec_wrapper",
    "base_env_wrapper_fn",
    "BaseAtariEnv",
    "ParallelAtariEnv",
]

AgentID = NewType("AgentID", str)
ObsType = NewType("ObsType", npt.NDArray[np.integer])
ActionType = NewType("ActionType", int)
AtariAECEnv = AECEnv[AgentID, ObsType, ActionType]
StateType = npt.NDArray[np.int8]


def base_env_wrapper_fn(
    raw_env_fn: Callable[..., AtariAECEnv]
) -> Callable[..., AtariAECEnv]:
    def env_fn(**kwargs: Any) -> AtariAECEnv:
        env = raw_env_fn(**kwargs)
        env = wrappers.AssertOutOfBoundsWrapper(env)
        env = wrappers.OrderEnforcingWrapper(env)
        return env

    return env_fn


def BaseAtariEnv(**kwargs: Any) -> AtariAECEnv:
    return parallel_to_aec_wrapper(ParallelAtariEnv(**kwargs))


class ParallelAtariEnv(ParallelEnv[AgentID, ObsType, ActionType], EzPickle):
    def __init__(
        self,
        game: str,
        num_players: int,
        mode_num: int | None = None,
        seed: int | None = None,
        obs_type: str = "rgb_image",
        full_action_space: bool = False,
        env_name: str | None = None,
        max_cycles: int = 100000,
        render_mode: str | None = None,
        auto_rom_install_path: str | None = None,
    ) -> None:
        """Initializes the `ParallelAtariEnv` class.

        Frameskip should be either a tuple (indicating a random range to
        choose from, with the top value exclude), or an int.
        """
        EzPickle.__init__(
            self,
            game=game,
            num_players=num_players,
            mode_num=mode_num,
            seed=seed,
            obs_type=obs_type,
            full_action_space=full_action_space,
            env_name=env_name,
            max_cycles=max_cycles,
            render_mode=render_mode,
            auto_rom_install_path=auto_rom_install_path,
        )

        assert obs_type in (
            "ram",
            "rgb_image",
            "grayscale_image",
        ), "obs_type must  either be 'ram' or 'rgb_image' or 'grayscale_image'"
        self.obs_type = obs_type
        self.full_action_space = full_action_space
        self.num_players = num_players
        self.max_cycles = max_cycles
        if env_name is None:
            env_name = "custom_" + game
        self.metadata = {
            "render_modes": ["human", "rgb_array"],
            "name": env_name,
            "render_fps": 60,
        }
        self.render_mode = render_mode

        multi_agent_ale_py.ALEInterface.setLoggerMode("error")
        self.ale = multi_agent_ale_py.ALEInterface()

        self.ale.setFloat(b"repeat_action_probability", 0.0)

        if auto_rom_install_path is None:
            start = Path(multi_agent_ale_py.__file__).parent
        else:
            start = Path(auto_rom_install_path).resolve()

        # start looking in local directory
        final = start / f"{game}.bin"
        if not final.exists():
            # if that doesn't work, look in 'roms'
            final = start / "roms" / f"{game}.bin"

        if not final.exists():
            # use old AutoROM install path as backup
            final = start / "ROM" / game / f"{game}.bin"

        if not final.exists():
            raise OSError(
                f"rom {game} is not installed. Please install roms using AutoROM tool (https://github.com/Farama-Foundation/AutoROM) "
                "or specify and double-check the path to your Atari rom using the `rom_path` argument."
            )

        self.rom_path = str(final)
        self.ale.loadROM(self.rom_path)

        all_modes = self.ale.getAvailableModes(num_players)

        if mode_num is None:
            mode = all_modes[0]
        else:
            mode = mode_num
            assert (
                mode in all_modes
            ), f"mode_num parameter is wrong. Mode {mode_num} selected, only {list(all_modes)} modes are supported"

        self.mode = mode
        self.ale.setMode(self.mode)
        assert num_players == self.ale.numPlayersActive()

        if full_action_space:
            action_size = 18
            action_mapping: npt.NDArray[np.int32] = np.arange(action_size)
        else:
            action_mapping = self.ale.getMinimalActionSet()
            action_size = len(action_mapping)

        self.action_mapping = action_mapping

        if obs_type == "ram":
            observation_space = gymnasium.spaces.Box(
                low=0, high=255, dtype=np.uint8, shape=(128,)
            )
        else:
            (screen_width, screen_height) = self.ale.getScreenDims()
            if obs_type == "rgb_image":
                num_channels = 3
            elif obs_type == "grayscale_image":
                num_channels = 1
            observation_space = spaces.Box(
                low=0,
                high=255,
                shape=(screen_height, screen_width, num_channels),
                dtype=np.uint8,
            )

        player_names = ["first", "second", "third", "fourth"]
        self.agents = [AgentID(f"{player_names[n]}_0") for n in range(num_players)]
        self.possible_agents = self.agents[:]

        self.action_spaces = {
            agent: gymnasium.spaces.Discrete(action_size)
            for agent in self.possible_agents
        }
        self.observation_spaces = {
            agent: observation_space for agent in self.possible_agents
        }

        self._screen: pygame.Surface | None = None
        self._seed(seed)

    def _seed(self, seed: int | None) -> None:
        self.np_random, seed = seeding.np_random(seed)
        self.ale.setInt(b"random_seed", seed)
        self.ale.loadROM(self.rom_path)
        self.ale.setMode(self.mode)

    def reset(
        self, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[dict[AgentID, ObsType], dict[AgentID, Any]]:
        if seed is not None:
            self._seed(seed=seed)
        else:
            self.np_random, seed = seeding.np_random()
        self.ale.reset_game()
        self.agents = self.possible_agents[:]
        self.terminations = {agent: False for agent in self.possible_agents}
        self.frame = 0

        obs = self._observe()
        infos: dict[AgentID, dict[str, Any]] = {
            agent: {} for agent in self.possible_agents if agent in self.agents
        }
        return {agent: obs for agent in self.agents}, infos

    def observation_space(self, agent: AgentID) -> gymnasium.spaces.Space[Any]:
        return self.observation_spaces[agent]

    def action_space(self, agent: AgentID) -> gymnasium.spaces.Space[Any]:
        return self.action_spaces[agent]

    def _observe(self) -> ObsType:
        if self.obs_type == "ram":
            bytes = self.ale.getRAM()
            return cast(ObsType, bytes)
        elif self.obs_type == "rgb_image":
            return cast(ObsType, self.ale.getScreenRGB())
        elif self.obs_type == "grayscale_image":
            return cast(ObsType, self.ale.getScreenGrayscale())
        else:
            raise ValueError(
                "obs_type must  either be 'ram' or 'rgb_image' or 'grayscale_image'"
            )

    def step(
        self, action_dict: dict[AgentID, ActionType]
    ) -> tuple[
        dict[AgentID, ObsType],
        dict[AgentID, float],
        dict[AgentID, bool],
        dict[AgentID, bool],
        dict[AgentID, dict[str, Any]],
    ]:
        actions: npt.NDArray[np.int32] = np.zeros(self.max_num_agents, dtype=np.int32)
        for i, agent in enumerate(self.possible_agents):
            if agent in action_dict:
                actions[i] = action_dict[agent]

        actions = self.action_mapping[actions]
        rewards = self.ale.act(actions)
        self.frame += 1
        truncations = {agent: self.frame >= self.max_cycles for agent in self.agents}

        if self.ale.game_over():
            terminations = {agent: True for agent in self.agents}
        else:
            lives = self.ale.allLives()
            # an inactive agent in ale gets a -1 life.
            terminations = {
                agent: int(life) < 0
                for agent, life in zip(self.possible_agents, lives)
                if agent in self.agents
            }

        obs = self._observe()
        observations = {agent: obs for agent in self.agents}
        rewards = {
            agent: rew
            for agent, rew in zip(self.possible_agents, rewards)
            if agent in self.agents
        }
        infos: dict[AgentID, dict[str, Any]] = {
            agent: {} for agent in self.possible_agents if agent in self.agents
        }
        self.agents = [agent for agent in self.agents if not terminations[agent]]

        if self.render_mode == "human":
            self.render()
        return observations, rewards, terminations, truncations, infos

    def render(self) -> npt.NDArray[np.integer] | None:
        if self.render_mode is None:
            gymnasium.logger.warn(
                "You are calling render method without specifying any render mode."
            )
            return None

        assert (
            self.render_mode in self.metadata["render_modes"]
        ), f"{self.render_mode} is not a valid render mode"
        (screen_width, screen_height) = self.ale.getScreenDims()
        image = cast(npt.NDArray[np.int8], self.ale.getScreenRGB())
        if self.render_mode == "human":
            zoom_factor = 4
            if self._screen is None:
                pygame.init()
                self._screen = pygame.display.set_mode(
                    (screen_width * zoom_factor, screen_height * zoom_factor)
                )

            myImage = pygame.image.frombuffer(
                image.tobytes(), image.shape[:2][::-1], "RGB"
            )

            myImage = pygame.transform.scale(
                myImage, (screen_width * zoom_factor, screen_height * zoom_factor)
            )

            self._screen.blit(myImage, (0, 0))

            pygame.display.flip()
        elif self.render_mode == "rgb_array":
            return image
        return None

    def close(self) -> None:
        if self._screen is not None:
            pygame.quit()
            self._screen = None

    def clone_state(self) -> StateType:
        """Clone emulator state w/o system state.

        Restoring this state will *not* give an identical environment.
        For complete cloning and restoring of the full state,
        see `{clone,restore}_full_state()`.
        """
        state_ref = self.ale.cloneState()
        state = cast(StateType, self.ale.encodeState(state_ref))
        self.ale.deleteState(state_ref)
        return state

    def restore_state(self, state: StateType) -> None:
        """Restore emulator state w/o system state."""
        state_ref = self.ale.decodeState(state)
        self.ale.restoreState(state_ref)
        self.ale.deleteState(state_ref)

    def clone_full_state(self) -> StateType:
        """Clone emulator state w/ system state including pseudorandomness.

        Restoring this state will give an identical environment.
        """
        state_ref = self.ale.cloneSystemState()
        state = cast(StateType, self.ale.encodeState(state_ref))
        self.ale.deleteState(state_ref)
        return state

    def restore_full_state(self, state: StateType) -> None:
        """Restore emulator state w/ system state including pseudorandomness."""
        state_ref = self.ale.decodeState(state)
        self.ale.restoreSystemState(state_ref)
        self.ale.deleteState(state_ref)
