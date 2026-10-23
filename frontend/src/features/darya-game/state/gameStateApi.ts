import { getData, putData } from '../../../api/client';
import type { GameStateSnapshot, GameStateUpdate } from '../contracts';

export const gameStateApi = {
  current(signal?: AbortSignal) {
    return getData<GameStateSnapshot>('/api/darya-game/state', undefined, signal);
  },
  replace(update: GameStateUpdate, signal?: AbortSignal) {
    return putData<GameStateSnapshot, GameStateUpdate>('/api/darya-game/state', update, signal);
  },
};
